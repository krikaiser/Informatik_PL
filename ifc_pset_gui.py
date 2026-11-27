"""Tkinter-Fenster für die lokale IFC-Attributprüfung."""

from __future__ import annotations

import sqlite3
import tkinter as tk
from concurrent.futures import Future, ProcessPoolExecutor
from datetime import date
from pathlib import Path
from tkinter import filedialog, font, ttk
from tkinter.scrolledtext import ScrolledText

from ifc_class_picker import ClassPicker
from ifc_pdf_report import write_pdf_report
from ifc_pset_check import (
    GROUP_HEADERS,
    OUTPUT_FOLDER,
    Audit,
    GroupRow,
    Rule,
    audit_file,
    display_percentage,
    parse_rule,
    read_model_classes,
    write_reports,
)
from ifc_rule_history import HISTORY_PATH, load_rule_history, save_rule_history


def create_table(parent, columns: tuple[tuple[str, int], ...], height: int = 7) -> ttk.Treeview:
    frame = ttk.Frame(parent)
    frame.pack(fill="both", expand=True)
    frame.columnconfigure(0, weight=1)
    frame.rowconfigure(0, weight=1)
    headings = [name for name, _ in columns]
    table = ttk.Treeview(frame, columns=headings, show="headings", height=height, selectmode="browse")
    for name, width in columns:
        table.heading(name, text=name, anchor="w")
        table.column(name, width=width, minwidth=width)
    vertical = ttk.Scrollbar(frame, orient="vertical", command=table.yview)
    horizontal = ttk.Scrollbar(frame, orient="horizontal", command=table.xview)
    table.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
    table.grid(row=0, column=0, sticky="nsew")
    vertical.grid(row=0, column=1, sticky="ns")
    horizontal.grid(row=1, column=0, sticky="ew")
    return table


class AuditWindow:
    def __init__(self, root: tk.Tk, *, history_path: Path = HISTORY_PATH):
        self.root = root
        root.title("Attributprüfung")
        root.geometry("1060x730")
        root.minsize(740, 620)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.executor: ProcessPoolExecutor | None = None
        self.future: Future | None = None
        self.path: Path | None = None
        self.model_classes: dict[str, tuple[str, ...]] = {}
        self.audit: Audit | None = None
        self.rules: list[Rule] = []
        self.history_path = history_path
        self.saved_rules: list[Rule] = []
        self.edit_index: int | None = None
        self.schema_findings: list[dict] = []

        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("Primary.TButton", background="#173F61", foreground="white", padding=(12, 6))
        style.map("Primary.TButton",
                  background=[("disabled", "#DADFE4"), ("pressed", "#143550"), ("active", "#285A82")],
                  foreground=[("disabled", "#566471")])
        style.configure("Results.TNotebook.Tab", padding=(15, 6))
        style.map("Results.TNotebook.Tab",
                  background=[("selected", "#173F61"), ("!selected", "#DADFE4")],
                  foreground=[("selected", "white"), ("!selected", "#344454")])

        content = ttk.Frame(root, padding=14)
        content.pack(fill="both", expand=True)
        file_row = ttk.Frame(content)
        file_row.pack(fill="x", pady=(0, 10))
        self.choose_button = ttk.Button(file_row, text="IFC-Datei auswählen", command=self.choose_file)
        self.choose_button.pack(side="left")
        self.file_text = tk.StringVar()
        ttk.Entry(file_row, textvariable=self.file_text, state="readonly").pack(
            side="left", fill="x", expand=True, padx=(8, 0),
        )

        rules_frame = ttk.LabelFrame(content, text="Regeln", padding=8)
        rules_frame.pack(fill="x")
        self.rule_table = create_table(rules_frame, (("PSet", 160), ("Properties", 280), ("IFC-Klassen", 240)), 3)
        self.rule_table.bind("<<TreeviewSelect>>", self.update_controls)
        actions = ttk.Frame(rules_frame)
        actions.pack(fill="x", pady=(6, 0))
        self.add_button = ttk.Button(actions, text="+", width=3, command=self.edit_rule)
        self.add_button.pack(side="left")
        self.edit_button = ttk.Button(actions, text="Bearbeiten", command=self.edit_selected)
        self.edit_button.pack(side="left", padx=6)
        self.editor = ttk.Frame(rules_frame, padding=(0, 6, 0, 0))
        self.editor.columnconfigure(3, weight=1)
        ttk.Label(self.editor, text="PSet").grid(row=0, column=0, sticky="w")
        self.pset_entry = ttk.Entry(self.editor, width=21)
        self.pset_entry.grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Label(self.editor, text="Properties").grid(row=0, column=2, sticky="w")
        self.properties_entry = ttk.Entry(self.editor)
        self.properties_entry.grid(row=0, column=3, sticky="ew", padx=4)
        ttk.Label(self.editor, text="IFC-Klassen").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.class_picker = ClassPicker(self.editor, self.confirm_rule)
        self.class_picker.grid(row=1, column=1, columnspan=3, sticky="ew", padx=4, pady=(6, 0))
        self.confirm_button = ttk.Button(self.editor, text="Übernehmen", command=self.confirm_rule)
        self.confirm_button.grid(row=1, column=4, padx=(0, 4), pady=(6, 0))
        for entry in (self.pset_entry, self.properties_entry, self.class_picker, self.confirm_button):
            entry.bind("<Return>", self.confirm_rule)
            entry.bind("<KP_Enter>", self.confirm_rule)
        ttk.Button(self.editor, text="Abbrechen", command=self.finish_edit).grid(row=1, column=5, pady=(6, 0))
        self.rule_message = tk.StringVar()
        ttk.Label(self.editor, textvariable=self.rule_message, wraplength=430).grid(
            row=2, column=0, columnspan=6, sticky="w",
        )

        self.history_row = ttk.Frame(rules_frame)
        self.history_row.pack(fill="x", pady=(6, 0))
        ttk.Label(self.history_row, text="Gespeicherte Regeln").pack(side="left")
        self.saved_rule_choice = ttk.Combobox(self.history_row, state="readonly")
        self.saved_rule_choice.set("Noch keine gespeicherten Regeln")
        self.saved_rule_choice.pack(side="left", fill="x", expand=True, padx=6)
        self.reuse_rule_button = ttk.Button(self.history_row, text="Übernehmen", command=self.use_saved_rule)
        self.reuse_rule_button.pack(side="left")
        self.history_message = ttk.Label(rules_frame)
        self.history_message.bind("<Configure>", self.wrap_label)

        run_row = ttk.Frame(content)
        run_row.pack(fill="x", pady=8)
        self.start_button = ttk.Button(run_row, text="Überprüfung starten", style="Primary.TButton", command=self.start)
        self.start_button.pack(side="left")
        self.status = tk.StringVar(value="IFC-Datei auswählen und mindestens eine Regel hinzufügen.")
        self.status_label = ttk.Label(run_row, textvariable=self.status)
        self.status_label.pack(side="left", fill="x", expand=True, padx=8)
        self.status_label.bind("<Configure>", self.wrap_label)

        self.export_row = ttk.Frame(content)
        self.export_buttons = (
            ttk.Button(self.export_row, text="CSV speichern", command=lambda: self.save_export(write_reports)),
            ttk.Button(self.export_row, text="PDF speichern", command=lambda: self.save_export(write_pdf_report)),
        )
        for button in self.export_buttons:
            button.pack(side="left", padx=(0, 6))
        self.scope = tk.StringVar()
        self.scope_label = ttk.Label(content, textvariable=self.scope)
        self.scope_label.pack(fill="x", pady=(0, 5))
        self.scope_label.bind("<Configure>", self.wrap_label)
        self.tabs = ttk.Notebook(content, style="Results.TNotebook")
        self.tabs.pack(fill="both", expand=True)
        attributes = ttk.Frame(self.tabs, padding=8)
        schema = ttk.Frame(self.tabs, padding=8)
        self.tabs.add(attributes, text="Attributprüfung")
        self.tabs.add(schema, text="Schema-Prüfung")
        group_columns = tuple(zip(GROUP_HEADERS, (155, 140, 180, 135, 125, 170, 230), strict=True))
        self.attribute_table = create_table(attributes, group_columns, height=5)
        for heading in GROUP_HEADERS[4:6]:
            self.attribute_table.column(heading, anchor="e")
        self.attribute_table.bind("<<TreeviewSelect>>", self.show_attribute_group)
        self.attribute_details = ScrolledText(attributes, height=5, wrap="word", state="disabled")
        self.attribute_details.pack(fill="x", pady=(5, 0))
        self.schema_status = tk.StringVar()
        schema_label = ttk.Label(schema, textvariable=self.schema_status)
        schema_label.pack(fill="x", pady=(0, 5))
        schema_label.bind("<Configure>", self.wrap_label)
        self.schema_table = create_table(schema, (
            ("STEP-ID", 75), ("Stufe", 70), ("Attribut", 155), ("Meldung", 320),
        ), height=5)
        self.schema_table.bind("<<TreeviewSelect>>", self.show_schema_finding)
        self.schema_details = ScrolledText(schema, height=5, wrap="word", state="disabled")
        self.schema_details.pack(fill="x", pady=(5, 0))
        try:
            self.refresh_saved_rules()
        except (OSError, sqlite3.Error, ValueError) as exc:
            self.show_history_message(f"Gespeicherte Regeln konnten nicht geladen werden. {exc}")
        self.update_controls()

    @staticmethod
    def wrap_label(event) -> None:
        event.widget.configure(wraplength=max(event.width, 1))

    def update_controls(self, _event=None) -> None:
        busy = self.future is not None
        editing = bool(self.editor.winfo_manager())
        can_edit = not busy and not editing and bool(self.model_classes)
        self.choose_button.state(["disabled" if busy or editing else "!disabled"])
        self.add_button.state(["!disabled" if can_edit else "disabled"])
        self.edit_button.state(["!disabled" if can_edit and self.rule_table.selection() else "disabled"])
        can_reuse = can_edit and bool(self.saved_rules)
        self.saved_rule_choice.configure(state="readonly" if can_reuse else "disabled")
        self.reuse_rule_button.state(["!disabled" if can_reuse else "disabled"])
        self.start_button.state(["!disabled" if can_edit and self.rules else "disabled"])
        for button in self.export_buttons:
            button.state(["disabled" if busy or editing else "!disabled"])

    def refresh_saved_rules(self) -> None:
        self.saved_rules = load_rule_history(self.history_path)
        self.saved_rule_choice.configure(values=[
            f"{rule.pset} | {', '.join(rule.properties)} | {', '.join(rule.classes)}" for rule in self.saved_rules
        ])
        if self.saved_rules:
            self.saved_rule_choice.current(0)
        else:
            self.saved_rule_choice.set("Noch keine gespeicherten Regeln")
        self.history_message.pack_forget()

    def show_history_message(self, message: str) -> None:
        self.history_message.configure(text=message)
        self.history_message.pack(fill="x", pady=(4, 0))

    def use_saved_rule(self) -> None:
        index = self.saved_rule_choice.current()
        if index < 0:
            return
        rule = self.saved_rules[index]
        if rule in self.rules:
            row = str(self.rules.index(rule))
            self.rule_table.selection_set(row)
            self.rule_table.see(row)
        else:
            self.update_rule_row(rule)
        self.rule_table.focus_set()
        self.update_controls()

    def choose_file(self) -> None:
        selected = filedialog.askopenfilename(
            parent=self.root, title="IFC-Datei auswählen", filetypes=[("IFC-Dateien", "*.ifc")],
        )
        if selected:
            self.path = Path(selected)
            self.file_text.set(str(self.path))
            self.model_classes = {}
            self.clear_results()
            self.status.set(f"IFC-Klassen für {self.path.name} werden geladen …")
            self.launch_job(read_model_classes, (self.path,), self.show_model_classes, "IFC-Datei konnte nicht geladen werden.")

    def show_model_classes(self, model_classes: dict[str, tuple[str, ...]]) -> None:
        self.model_classes = model_classes
        if self.model_classes:
            self.status.set(f"{len(self.model_classes)} IFC-Klassen geladen. Regeln hinzufügen oder auswählen.")
        else:
            self.status.set("Die IFC-Datei enthält keine Klasseninstanzen.")
        self.finish_job()

    def edit_selected(self) -> None:
        if selection := self.rule_table.selection():
            self.edit_rule(int(selection[0]))

    def edit_rule(self, index: int | None = None) -> None:
        self.edit_index = index
        self.pset_entry.delete(0, "end")
        self.properties_entry.delete(0, "end")
        selected = ()
        if index is not None:
            rule = self.rules[index]
            self.pset_entry.insert(0, rule.pset)
            self.properties_entry.insert(0, ",".join(rule.properties))
            selected = tuple(name for name, ancestors in self.model_classes.items() if rule.applies_to(name, ancestors))
        self.class_picker.set_classes(tuple(self.model_classes), selected)
        self.rule_message.set("")
        self.editor.pack(fill="x", before=self.history_row)
        self.pset_entry.focus_set()
        self.update_controls()

    def confirm_rule(self, _event=None) -> str:
        try:
            rule = parse_rule(self.pset_entry.get(), self.properties_entry.get(), self.class_picker.selection())
        except ValueError as exc:
            self.rule_message.set(str(exc))
            return "break"
        self.update_rule_row(rule, self.edit_index)
        self.finish_edit()
        return "break"

    def update_rule_row(self, rule: Rule, index: int | None = None) -> None:
        values = (rule.pset, ", ".join(rule.properties), ", ".join(rule.classes))
        if index is None:
            row = str(len(self.rules))
            self.rules.append(rule)
            self.rule_table.insert("", "end", iid=row, values=values)
        else:
            row = str(index)
            self.rules[index] = rule
            self.rule_table.item(row, values=values)
        self.rule_table.selection_set(row)
        self.rule_table.see(row)
        table_font = font.nametofont("TkDefaultFont", root=self.root)
        for column, value in zip(self.rule_table["columns"], values):
            width = table_font.measure(value) + 16
            if width > self.rule_table.column(column, "width"):
                self.rule_table.column(column, width=width, minwidth=width)

    def finish_edit(self) -> None:
        self.class_picker.close_menu()
        self.editor.pack_forget()
        self.edit_index = None
        self.update_controls()
        self.rule_table.focus_set()

    def clear_results(self) -> None:
        self.audit = None
        self.export_row.pack_forget()
        self.attribute_table.delete(*self.attribute_table.get_children())
        self.show_attribute_group()
        self.schema_table.delete(*self.schema_table.get_children())
        self.schema_findings = []
        self.show_schema_finding()
        self.scope.set("")
        self.schema_status.set("")

    def start(self) -> None:
        if self.start_button.instate(["disabled"]):
            return
        self.clear_results()
        self.status.set(f"{self.path.name} wird geprüft …")
        rules = tuple(self.rules)
        if not self.launch_job(audit_file, (self.path, rules), self.show_results, "Prüfung fehlgeschlagen."):
            return
        try:
            save_rule_history(self.history_path, rules)
            self.refresh_saved_rules()
        except (OSError, sqlite3.Error, ValueError) as exc:
            self.show_history_message(f"Regelverlauf konnte nicht aktualisiert werden. {exc}")

    def launch_job(self, operation, arguments, on_result, failure: str) -> bool:
        try:
            self.executor = ProcessPoolExecutor(max_workers=1)
            self.future = self.executor.submit(operation, *arguments)
        except (OSError, RuntimeError) as exc:
            self.stop_worker()
            self.status.set(f"{failure} {exc}")
            self.update_controls()
            return False
        self.update_controls()
        self.root.after(75, self.poll, on_result, failure)
        return True

    def poll(self, on_result, failure: str) -> None:
        if not self.future.done():
            self.root.after(75, self.poll, on_result, failure)
            return
        self.stop_worker()
        try:
            result = self.future.result()
        except Exception as exc:
            self.status.set(f"{failure} {exc}")
            self.finish_job()
            return
        on_result(result)

    def finish_job(self) -> None:
        self.future = None
        self.update_controls()

    def show_results(self, audit: Audit) -> None:
        self.audit = audit
        model, schema, groups = audit.summary, audit.schema, audit.groups
        share = display_percentage(model["Anteil_Objekte_mit_Befund_an_allen_Objekten_Prozent"])
        self.scope.set(
            f'{model["Objekte_im_Zählbereich"]} Objekte im Zählbereich · '
            f'{model["Objekte_geprüft"]} geprüft · '
            f'{model["Geprüfte_Objekte_mit_Befund"]} mit Attributbefund · Befundquote {share} · '
            f'{len(groups)} Befundgruppen mit {model["Anzahl_Attributbefunde"]} Einzelbefunden'
        )
        self.schema_findings = schema["Befunde"]
        status = f'{schema["Schema"]} · {schema["Anzahl_Befunde"]} Befunde'
        completion = f'Prüfung abgeschlossen für {model["IFC"]}.'
        if schema.get("Fehler"):
            status += f' · Prüfung unvollständig. {schema["Fehler"]}'
            completion = f'Attributprüfung abgeschlossen für {model["IFC"]}. Schema-Prüfung unvollständig.'
        self.schema_status.set(status)
        self.append_findings(groups, completion, 0)

    def append_findings(self, groups: list[GroupRow], completion: str, start: int) -> None:
        stop = start + 250
        for group in groups[start:stop]:
            self.attribute_table.insert("", "end", values=group)
        for index, finding in enumerate(self.schema_findings[start:stop], start):
            step_id = f'#{finding["STEP-ID"]}' if finding["STEP-ID"] else ""
            self.schema_table.insert("", "end", iid=str(index), values=(
                step_id, finding["Stufe"], finding["Attribut"], " ".join(finding["Meldung"].split()),
            ))
        if stop < max(len(groups), len(self.schema_findings)):
            self.root.after(1, self.append_findings, groups, completion, stop)
            return
        self.status.set(completion)
        self.export_row.pack(fill="x", pady=(0, 7), before=self.scope_label)
        self.finish_job()

    def save_export(self, writer) -> None:
        if self.audit is None or self.future is not None:
            return
        output = self.audit.path.parent / OUTPUT_FOLDER
        self.status.set("Bericht wird gespeichert …")
        self.launch_job(writer, (output, self.audit, date.today()), self.show_saved_files, "Export fehlgeschlagen.")

    def show_saved_files(self, files: tuple[Path, ...]) -> None:
        names = ", ".join(file.name for file in files)
        self.status.set(f"Gespeichert in {files[0].parent} · {names}")
        self.finish_job()

    def show_attribute_group(self, _event=None) -> None:
        self.attribute_details.configure(state="normal")
        self.attribute_details.delete("1.0", "end")
        if selection := self.attribute_table.selection():
            row = self.attribute_table.item(selection[0], "values")
            text = f"{GROUP_HEADERS[-1]}\n{row[-1]}"
            if row[3]:
                text = f"{GROUP_HEADERS[3]}\n{row[3]}\n\n{text}"
            self.attribute_details.insert("1.0", text)
        self.attribute_details.configure(state="disabled")

    def show_schema_finding(self, _event=None) -> None:
        self.schema_details.configure(state="normal")
        self.schema_details.delete("1.0", "end")
        if selection := self.schema_table.selection():
            finding = self.schema_findings[int(selection[0])]
            text = "\n\n".join(part for part in (finding["Instanz"], finding["Meldung"]) if part)
            self.schema_details.insert("1.0", text)
        self.schema_details.configure(state="disabled")

    def stop_worker(self) -> None:
        if self.executor is not None:
            self.executor.shutdown(wait=False, cancel_futures=True)
            self.executor = None

    def close(self) -> None:
        self.stop_worker()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    AuditWindow(root)
    root.mainloop()
