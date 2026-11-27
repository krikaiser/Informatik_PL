"""Attribut- und Schemaprüfung für IFC-Dateien. Python 3.11+ und IfcOpenShell.

Installation: python -m pip install -r requirements.txt
Fenster öffnen: python ifc_pset_check.py
Beispielregeln mit CSV-Export ausführen: python ifc_pset_check.py "Bestand.ifc"
"""

from __future__ import annotations

import argparse
import csv
import re
import shutil
import sys
import tempfile
from collections import Counter, defaultdict
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import date
from pathlib import Path

try:
    import ifcopenshell
    import ifcopenshell.validate as validator
    from ifcopenshell.util.element import get_property_definition
except ImportError as error:
    raise SystemExit("IfcOpenShell installieren: python -m pip install ifcopenshell") from error


@dataclass(frozen=True)
class Rule:
    """Sollvorgabe aus Pset-Name, betroffenen IFC-Klassen und Pflichtfeldern."""

    pset: str
    classes: tuple[str, ...]
    properties: tuple[str, ...]
    include_subtypes: bool = True

    def applies_to(self, name: str, ancestors: tuple[str, ...]) -> bool:
        return name in self.classes or (self.include_subtypes and any(parent in self.classes for parent in ancestors))


# Diese Beispielregeln enthalten fünf Felder für DB und drei für DB_Allgemeine-Daten.
# Bei der Klassenabfrage zählen auch Unterklassen. IfcElement erfasst z. B. IfcStair.
# Pset- und Feldnamen werden exakt verglichen, einschließlich eines Doppelpunkts im Namen.
DEMO_RULES = (
    Rule(
        "DB",
        ("IfcElement",),
        ("DB_Anlagentyp", "Objektklasse", "Fachmodell", "Datenquelle", "Zustand"),
    ),
    Rule(
        "DB_Allgemeine-Daten",
        (
            "IfcWall", "IfcFooting", "IfcRailing", "IfcSpace", "IfcCovering",
            "IfcSlab", "IfcSite", "IfcBuilding", "IfcBuildingElementProxy",
            "IfcBeam", "IfcMember", "IfcColumn", "IfcFlowTerminal", "IfcFlowController",
            "IfcFlowSegment", "IfcFlowFitting", "IfcStair", "IfcStairFlight",
            "IfcRoof", "IfcDoor",
        ),
        ("Bahnhofsnummer", "Bahnhofskategorie", "Durchfahrtsgeschwindigkeit"),
    ),
)
CLASS_PROFILES = {rule.pset: rule.classes for rule in DEMO_RULES}


def parse_rule(pset: str, properties: str, classes: tuple[str, ...] | None = None) -> Rule:
    """Erzeugt aus einer bestätigten Eingabe eine Regel mit festem Klassenprofil."""
    if not pset.strip():
        raise ValueError("Bitte einen PSet-Namen eingeben.")
    names = tuple(properties.split(","))
    if any(not name.strip() for name in names):
        raise ValueError("Bitte für jeden Listeneintrag einen Attributnamen eingeben.")
    if any(name != name.strip() for name in names):
        raise ValueError("Bitte Attributnamen ohne Leerzeichen am Anfang, am Ende oder am Komma eingeben.")
    if classes is not None:
        if not classes:
            raise ValueError("Bitte mindestens eine IFC-Klasse auswählen.")
        return Rule(pset, classes, names, include_subtypes=False)
    return Rule(pset, CLASS_PROFILES.get(pset, ("IfcElement",)), names)


STATUS_TEXT = {
    "PSET_MISSING": "Pset fehlt",
    "PROPERTY_MISSING": "Attribut fehlt",
    "WRONG_PSET": "Attribut im falschen Pset",
    "EMPTY_VALUE": "Wert leer",
    "DUPLICATE_PROPERTY": "Attribut doppelt vorhanden",
}
DEMO_NOTICE = "Beispielregeln. Pflichtfelder und IFC-Klassen vor Projekteinsatz bestätigen."
OUTPUT_FOLDER = "IFC_Pruefung"

GROUP_HEADERS = (
    "Pset", "Attribut", "Befund", "Gefunden in", "Betroffene Objekte",
    "Anteil an allen Objekten (%)", "IFC-Klassen (betroffene Objekte)",
)
GroupRow = tuple[str, str, str, str, int, str, str]


@dataclass(frozen=True)
class ObjectRef:
    """Objektbezug eines Befunds mit lokaler STEP-ID und IFC-GlobalId."""

    step_id: int
    guid: str
    ifc_class: str
    name: str


@dataclass(frozen=True)
class Finding:
    """Ein festgestellter Befund an einem Objekt, Pset oder Pflichtfeld."""

    obj: ObjectRef
    code: str
    pset: str
    prop: str = ""
    locations: tuple[tuple[str | None, int], ...] = ()

    @property
    def detail(self) -> str:
        return format_locations(self.code, self.locations)


@dataclass
class Audit:
    """Prüfergebnis des gelesenen Modellstands."""

    path: Path
    findings: list[Finding]
    groups: list[GroupRow]
    schema: dict
    summary: dict


class SchemaLogger(validator.json_logger):
    def set_state(self, key, value) -> None:
        if key == "instance":
            self.state.pop("attribute", None)
        super().set_state(key, value)


def has_value(value: object) -> bool:
    """Prüft, ob ein Wert für die Vollständigkeitsprüfung als befüllt gilt."""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple)):
        return any(has_value(item) for item in value)
    if isinstance(value, ifcopenshell.entity_instance) and value.id() == 0:
        return has_value(value.wrappedValue)
    return True  # Auch 0 und False sind befüllte Werte.


def property_has_value(prop: ifcopenshell.entity_instance, parents: frozenset[int] = frozenset()) -> bool:
    """Prüft den Wert eines IFC-Attributs unabhängig von seinen Metadaten."""
    match prop.is_a():
        case "IfcPropertySingleValue":
            return has_value(prop.NominalValue)
        case "IfcPropertyEnumeratedValue":
            return has_value(prop.EnumerationValues)
        case "IfcPropertyListValue":
            return has_value(prop.ListValues)
        case "IfcPropertyBoundedValue":
            return has_value((
                prop.LowerBoundValue, prop.UpperBoundValue, getattr(prop, "SetPointValue", None),
            ))
        case "IfcPropertyTableValue":
            return has_value((prop.DefiningValues, prop.DefinedValues))
        case "IfcPropertyReferenceValue":
            return prop.PropertyReference is not None
        case "IfcComplexProperty":
            if prop.id() in parents:
                return False
            return any(property_has_value(child, parents | {prop.id()}) for child in prop.HasProperties or ())
    return False


def percentage(count: int, total: int) -> float | None:
    """Berechnet einen Prozentanteil. Ohne Bezugsmenge ist er nicht definiert."""
    return round(100 * count / total, 2) if total else None


def read_properties(
    obj: ifcopenshell.entity_instance, names: set[str]
) -> tuple[set[str | None], dict[str, list[tuple[str | None, bool]]]]:
    """Liest Psets und einzelne Vorkommen der geforderten Felder."""
    definitions = {}
    if obj.is_a("IfcTypeObject"):
        definitions = {pset.id(): pset for pset in obj.HasPropertySets or ()}
    else:
        # Nur direkt zugeordnete Psets lesen. Psets eines verknüpften
        # IFC-Typs werden bei der Prüfung eines Exemplars nicht vererbt.
        for relation in getattr(obj, "IsDefinedBy", ()) or ():
            if not relation.is_a("IfcRelDefinesByProperties"):
                continue
            definition = relation.RelatingPropertyDefinition
            if definition is None:
                continue
            members = definition.wrappedValue if definition.is_a("IfcPropertySetDefinitionSet") else (definition,)
            definitions.update((pset.id(), pset) for pset in members)

    pset_names = set()
    occurrences = defaultdict(list)
    for pset in definitions.values():
        pset_name = pset.Name
        if pset.is_a("IfcPropertySet"):
            pset_names.add(pset_name)
            # Die STEP-ID entfernt wiederholte Verweise auf dieselbe Property
            # innerhalb eines Psets. Verschiedene gleichnamige Properties bleiben erhalten.
            properties = {prop.id(): prop for prop in pset.HasProperties or () if prop.Name in names}
            for prop in properties.values():
                occurrences[prop.Name].append((pset_name, property_has_value(prop)))
        elif pset.is_a("IfcPreDefinedPropertySet"):
            pset_names.add(pset_name)
            values = get_property_definition(pset)
            for name in names.intersection(values.keys() - {"id"}):
                occurrences[name].append((pset_name, has_value(values[name])))
    return pset_names, occurrences


def validate_schema_structure(model: ifcopenshell.file, logger: SchemaLogger) -> None:
    """Prüft die Schema-Struktur, falls eine Zusatzprüfung des Validators abbricht."""
    validator.validate_ifc_header(model, logger)
    schema = ifcopenshell.schema_by_name(model.schema_identifier)
    for instance in model:
        logger.set_state("instance", instance)
        entity, attributes = validator.get_entity_attributes(schema, instance.is_a())
        if entity.is_abstract():
            logger.error("Entity %s is abstract", entity.name())

        for index, (attribute, derived) in enumerate(zip(attributes, entity.derived())):
            if derived:
                continue
            logger.set_state("attribute", f"{entity.name()}.{attribute.name()}")
            try:
                value = instance[index]
                if value is not None:
                    validator.assert_valid(attribute.type_of_attribute(), value, schema, attr=attribute)
                elif not attribute.optional():
                    logger.error("Attribute not optional")
            except (validator.ValidationError, RuntimeError, TypeError, ValueError) as exc:
                logger.error("%s", exc)

        for attribute in entity.all_inverse_attributes():
            logger.set_state("attribute", f"{entity.name()}.{attribute.name()}")
            try:
                validator.assert_valid_inverse(attribute, getattr(instance, attribute.name()), schema)
            except (validator.ValidationError, RuntimeError, TypeError, ValueError) as exc:
                logger.error("%s", exc)


def validate_schema(model: ifcopenshell.file, path: Path) -> dict:
    """Validiert das bereits gelesene Modell mit seinem vollständigen Schema-Identifier."""
    logger = SchemaLogger()
    error = None
    wrapper = ifcopenshell.ifcopenshell_wrapper
    derived = wrapper.get_feature("use_attribute_value_derived")
    try:
        try:
            validator.validate(model, logger)
        except (TypeError, ValueError):
            # Zusatzprüfungen erwarten z. B. bei GlobalId schon einen String.
            # Ein falscher Datentyp darf die Prüfung späterer Instanzen nicht verhindern.
            logger = SchemaLogger()
            validate_schema_structure(model, logger)
    except (ifcopenshell.Error, RuntimeError, ValueError, TypeError) as exc:
        error = str(exc)
        logger.error("Schema-Prüfung abgebrochen: %s", error)
    finally:
        wrapper.set_feature("use_attribute_value_derived", derived)

    # Ergänzt Parserbefunde etwa zu Entity-Namen und der Anzahl der STEP-Argumente.
    logger.state.clear()
    logger.set_state("type", "schema")
    validator.log_internal_cpp_errors(model, str(path), logger)
    findings = []
    for statement in logger.statements:
        instance = statement.get("instance")
        findings.append({
            "Stufe": statement["level"],
            "Meldung": statement["message"],
            "STEP-ID": instance.id() if isinstance(instance, ifcopenshell.entity_instance) else None,
            "Instanz": str(instance) if instance is not None else "",
            "Attribut": statement.get("attribute") or "",
        })
    result = {
        "Schema": model.schema_identifier,
        "Status": "unvollständig" if error is not None else "mit Befunden" if findings else "ohne Befund",
        "Anzahl_Befunde": len(findings),
        "Befunde": findings,
    }
    if error is not None:
        result["Fehler"] = error
    return result


def open_ifc(path: Path) -> ifcopenshell.file:
    if path.suffix.lower() != ".ifc":
        raise ValueError(f"Ungültige Dateiendung für eine IFC-Datei: {path}")
    before = path.stat()
    ifcopenshell.get_log()
    ifcopenshell.ifcopenshell_wrapper.set_log_format_json()
    model = ifcopenshell.open(path, readonly=True)
    after = path.stat()
    if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
        raise OSError(f"Die IFC-Datei wurde beim Lesen geändert. Bitte die Prüfung erneut starten: {path}")
    return model


def model_class_hierarchy(model: ifcopenshell.file) -> dict[str, tuple[str, ...]]:
    """Liefert die vorkommenden Klassen mit ihren Oberklassen."""
    schema = ifcopenshell.schema_by_name(model.schema_identifier)
    classes = {}
    for name in sorted(model.types()):
        parent = schema.declaration_by_name(name).supertype()
        ancestors = []
        while parent is not None:
            ancestors.append(parent.name())
            parent = parent.supertype()
        classes[name] = tuple(ancestors)
    return classes


def read_model_classes(path: Path) -> dict[str, tuple[str, ...]]:
    return model_class_hierarchy(open_ifc(path))


def audit_file(path: Path, rules: tuple[Rule, ...]) -> Audit:
    """Liest eine IFC-Datei und ermittelt die Befunde pro Regel und Objekt."""
    model = open_ifc(path)
    schema = validate_schema(model, path)
    findings = []
    total = checked = affected = 0
    for name, ancestors in model_class_hierarchy(model).items():
        class_rules = {}
        for rule in rules:
            if rule.applies_to(name, ancestors):
                class_rules.setdefault(rule.pset, {}).update(dict.fromkeys(rule.properties))
        if not class_rules and "IfcElement" not in (name, *ancestors):
            continue
        objects = model.by_type(name, include_subtypes=False)
        total += len(objects)
        if class_rules:
            checked += len(objects)
            affected += audit_objects(objects, class_rules, findings)

    findings.sort(key=lambda finding: finding.obj.step_id)
    summary = {
        "IFC": path.name,
        "Objekte_im_Zählbereich": total,
        "Objekte_geprüft": checked,
        "Geprüfte_Objekte_mit_Befund": affected,
        "Anteil_Objekte_mit_Befund_an_allen_Objekten_Prozent": percentage(affected, total),
        "Anzahl_Attributbefunde": len(findings),
    }
    return Audit(path, findings, group_findings(findings, total), schema, summary)


def audit_objects(objects, object_rules, findings: list[Finding]) -> int:
    names = {prop for properties in object_rules.values() for prop in properties}
    affected = 0
    for obj in objects:
        guid = str(getattr(obj, "GlobalId", "") or "")
        ref = ObjectRef(obj.id(), guid, obj.is_a(), str(getattr(obj, "Name", "") or ""))

        psets, occurrences = read_properties(obj, names)
        first_finding = len(findings)
        for pset, properties in object_rules.items():
            if pset not in psets:
                # Ein fehlendes Pset ergibt einen Sammelbefund für dieses Objekt.
                findings.append(Finding(ref, "PSET_MISSING", pset))
            for prop in properties:
                found = occurrences.get(prop, ())
                counts = Counter(name for name, _ in found)
                locations = tuple(sorted(counts.items(), key=lambda entry: (entry[0] is None, entry[0] or "")))
                # Auch bei fehlendem Soll-Pset können mehrere Kopien in anderen
                # Psets vorliegen. Die Anzahl je Pset macht den Befund prüfbar.
                if len(found) > 1:
                    findings.append(Finding(ref, "DUPLICATE_PROPERTY", pset, prop, locations))

                if pset not in psets:
                    continue
                if pset not in counts:
                    code = "WRONG_PSET" if locations else "PROPERTY_MISSING"
                    found_in = tuple((name, 1) for name, _ in locations)
                    findings.append(Finding(ref, code, pset, prop, found_in))
                elif not any(filled for name, filled in found if name == pset):
                    findings.append(Finding(ref, "EMPTY_VALUE", pset, prop))
        if len(findings) > first_finding:
            affected += 1
    return affected


def format_locations(code: str, locations: tuple[tuple[str | None, int], ...]) -> str:
    if code == "DUPLICATE_PROPERTY":
        return ", ".join(f"{name or '(ohne Pset-Name)'} ({count}×)" for name, count in locations)
    return ", ".join(name or "(ohne Pset-Name)" for name, _ in locations)


def group_findings(findings: list[Finding], total: int) -> list[GroupRow]:
    """Fasst Befunde für die Ergebnisansicht und den Gruppenbericht zusammen."""
    groups = defaultdict(dict)
    for finding in findings:
        key = finding.pset, finding.prop, finding.code, finding.locations
        groups[key][finding.obj.step_id] = finding.obj.ifc_class

    ordered = sorted(groups, key=lambda key: (-len(groups[key]), *key[:3], format_locations(*key[2:])))
    rows = []
    for pset, prop, code, locations in ordered:
        objects = groups[pset, prop, code, locations]
        classes = ", ".join(
            f"{name} ({count})" for name, count in sorted(
                Counter(objects.values()).items(), key=lambda entry: (-entry[1], entry[0])
            )
        )
        share = percentage(len(objects), total)
        share_cell = "" if share is None else f"{share:.2f}".replace(".", ",")
        rows.append((
            pset, prop, STATUS_TEXT[code], format_locations(code, locations), len(objects), share_cell, classes,
        ))
    return rows


def write_groups_csv(path: Path, groups: list[GroupRow]) -> None:
    """Schreibt die in der Ergebnisansicht dargestellten Befundgruppen."""
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream, delimiter=";")
        writer.writerow(GROUP_HEADERS)
        for row in groups:
            writer.writerow(excel_safe_cell(value) if isinstance(value, str) else value for value in row)


def excel_safe_cell(value: str) -> str:
    """Verhindert, dass Excel übernommene IFC-Texte als Formeln ausführt."""
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value


def write_findings_csv(path: Path, findings: list[Finding]) -> None:
    """Schreibt die Einzelbefunde mit Objektbezug."""
    header = ["STEP-ID", "IFC-Klasse", "Objektname", "GlobalId", "Pset", "Attribut", "Befund", "Gefunden in"]
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream, delimiter=";")
        writer.writerow(header)
        for finding in findings:
            obj = finding.obj
            row = [
                f"#{obj.step_id}", obj.ifc_class, obj.name, obj.guid,
                finding.pset, finding.prop,
                STATUS_TEXT[finding.code], finding.detail,
            ]
            writer.writerow(excel_safe_cell(value) for value in row)


def publish_export(output: Path, staged: tuple[Path, ...]) -> tuple[Path, ...]:
    """Reserviert für alle Dateien dieselbe laufende Nummer."""
    patterns = [
        re.compile(rf"{re.escape(path.stem)}(?:_(\d+))?{re.escape(path.suffix)}", re.IGNORECASE)
        for path in staged
    ]
    number = 1
    for existing in output.iterdir():
        for pattern in patterns:
            if match := pattern.fullmatch(existing.name):
                number = max(number, int(match[1] or 1) + 1)
    while True:
        suffix = "" if number == 1 else f"_{number}"
        destinations = tuple(output / f"{file.stem}{suffix}{file.suffix}" for file in staged)
        reserved = []
        try:
            with ExitStack() as streams:
                files = []
                for path in destinations:
                    files.append(streams.enter_context(path.open("xb")))
                    reserved.append(path)
                for source, destination in zip(staged, files, strict=True):
                    with source.open("rb") as stream:
                        shutil.copyfileobj(stream, destination)
        except OSError as exc:
            for path in reserved:
                path.unlink(missing_ok=True)
            if isinstance(exc, FileExistsError) and len(reserved) < len(staged):
                number += 1
                continue
            raise
        return destinations


def write_reports(output: Path, audit: Audit, export_date: date) -> tuple[Path, ...]:
    """Schreibt die QS-Übersicht und die Fehlerliste als zusammengehöriges Paar."""
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output) as directory:
        stage = Path(directory)
        overview = stage / f"{export_date:%d%m%y}_QS_{audit.path.stem}.csv"
        findings = stage / f"{export_date:%d%m%y}_QS_Fehlerliste_{audit.path.stem}.csv"
        write_groups_csv(overview, audit.groups)
        write_findings_csv(findings, audit.findings)
        return publish_export(output, (overview, findings))


def display_percentage(share: float | None) -> str:
    return "nicht berechenbar" if share is None else f"{share:.2f}".replace(".", ",") + " %"


def main() -> None:
    if len(sys.argv) == 1:
        from ifc_pset_gui import main as show_window

        show_window()
        return

    parser = argparse.ArgumentParser(description="IFC-Attribut- und Schemaprüfung mit CSV-Berichten.")
    parser.add_argument("ifc", help="zu prüfende IFC-Datei")
    parser.add_argument("--out", help="Ausgabeordner")
    args = parser.parse_args()
    path = Path(args.ifc).expanduser().resolve()
    output = Path(args.out).expanduser().resolve() if args.out else path.parent / OUTPUT_FOLDER
    run_date = date.today()
    print(DEMO_NOTICE, flush=True)
    print(f"Prüfe {path.name} …", flush=True)
    try:
        audit = audit_file(path, DEMO_RULES)
        write_reports(output, audit, run_date)
    except (OSError, ValueError, ifcopenshell.Error) as exc:
        parser.exit(1, f"Prüfung fehlgeschlagen: {exc}\n")

    affected = audit.summary["Geprüfte_Objekte_mit_Befund"]
    total = audit.summary["Objekte_im_Zählbereich"]
    share = audit.summary["Anteil_Objekte_mit_Befund_an_allen_Objekten_Prozent"]
    print(f"{path.name}: {affected} von {total} Objekten mit Befund ({display_percentage(share)}).", flush=True)
    print(f'{audit.schema["Schema"]}: {audit.schema["Anzahl_Befunde"]} Schema-Befunde.', flush=True)
    print(f"CSV-Berichte: {output}", flush=True)


if __name__ == "__main__":
    main()
