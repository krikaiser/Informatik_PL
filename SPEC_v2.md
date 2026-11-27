# Attributprüfung

Attributprüfung ist eine lokale Windows-Desktopanwendung. Sie prüft die Pflichtattribute eines IFC-Fachmodells und validiert das Modell gegen sein IFC-Schema. Pro Prüflauf wird eine IFC-Datei schreibgeschützt gelesen.

## Bedienung

1. **IFC-Datei auswählen** öffnet den Windows-Dateidialog mit dem Filter `*.ifc`. Nach der Auswahl zeigt das Fenster den Dateipfad an.
2. **+** öffnet die Felder **PSet**, **Properties** und **IFC-Klassen**. In **PSet** steht der erwartete Eigenschaftssatz. In **Properties** werden die Pflichtattribute mit Kommas getrennt, ohne Leerzeichen vor oder nach dem Komma, etwa `Bahnhofsnummer,Bahnhofskategorie`. Beide Felder und jeder Listeneintrag müssen einen Namen enthalten.
3. Das Dropdown **IFC-Klassen** listet die in der geladenen Datei vorkommenden Klassen mit Kontrollkästchen auf. **Alle** steht oben und wählt sämtliche Klassen aus. Ab drei Suchzeichen filtert die Liste während der Eingabe. **Übernehmen** oder die Eingabetaste bestätigt eine gültige Regel mit mindestens einer ausgewählten Klasse. Die Regelübersicht zeigt PSet, Pflichtattribute und ausgewählte Klassen. **Bearbeiten** öffnet eine vorhandene Regel erneut.
4. Unter **Gespeicherte Regeln** stehen die bei früheren Prüfläufen verwendeten Regeln zur Auswahl. **Übernehmen** fügt die gewählte Regel zur aktuellen Regelübersicht hinzu. Dort lässt sie sich wie eine neu eingegebene Regel bearbeiten.
5. **Überprüfung starten** startet die Attribut- und die Schema-Prüfung. Die hervorgehobene Schaltfläche ist verfügbar, sobald eine Datei geladen, mindestens eine Regel bestätigt und die Regelbearbeitung abgeschlossen ist. Während des Prüflaufs zeigt das Fenster den Status an und bleibt bedienbar. Ein weiterer Prüflauf ist nach Abschluss möglich. Die Tabellenüberschriften sind linksbündig.

Beim Start eines Prüflaufs speichert die Anwendung PSet, Pflichtattribute und IFC-Klassen der verwendeten Regeln lokal unter `%LOCALAPPDATA%\Attributpruefung\Regeln.sqlite3`. Die zuletzt verwendeten Regeln stehen oben in der Liste. Identische Regeln erscheinen einmal. Der Regelverlauf steht nach einem Neustart und für andere IFC-Dateien zur Verfügung.

## Prüfumfang und Attributprüfung

Pset- und Attributnamen werden exakt verglichen. Bei Exemplaren zählen die direkt zugeordneten IFC-Eigenschaftssätze. Eine im Fenster erfasste Regel prüft die konkret ausgewählten Klassen. **Alle** umfasst auch Geometrie- und weitere Hilfsklassen der IFC-Datei. Bei mehreren Regeln zum selben Pset wird jedes konfigurierte Pflichtattribut pro Instanz einmal geprüft.

Die Beispielregeln für den Kommandozeilenaufruf und ältere gespeicherte Regeln verwenden Klassenprofile einschließlich der Unterklassen. Für Psets ohne hinterlegtes Profil gilt bei älteren Regeln `IfcElement`. Die Beispielprofile verwenden folgende Klassen.

| PSet | IFC-Klassen |
| --- | --- |
| `DB` | `IfcElement` |
| `DB_Allgemeine-Daten` | `IfcWall`, `IfcFooting`, `IfcRailing`, `IfcSpace`, `IfcCovering`, `IfcSlab`, `IfcSite`, `IfcBuilding`, `IfcBuildingElementProxy`, `IfcBeam`, `IfcMember`, `IfcColumn`, `IfcFlowTerminal`, `IfcFlowController`, `IfcFlowSegment`, `IfcFlowFitting`, `IfcStair`, `IfcStairFlight`, `IfcRoof`, `IfcDoor` |

| Befund | Bedingung am Objekt |
| --- | --- |
| Pset fehlt | Das Soll-Pset fehlt. Dafür entsteht ein Befund je Objekt und Soll-Pset. |
| Attribut fehlt | Das Soll-Pset ist vorhanden, das Pflichtattribut fehlt in allen Psets. |
| Attribut im falschen Pset | Das Soll-Pset ist vorhanden, das Pflichtattribut steht ausschließlich in anderen Psets. |
| Wert leer | Alle Werte des Attributs im Soll-Pset sind leer. Bei zusammengesetzten Attributen zählen auch die enthaltenen Werte. `0` und `False` gelten als befüllt. |
| Attribut doppelt vorhanden | Das Pflichtattribut kommt mehrfach vor, innerhalb eines Psets oder verteilt über mehrere Psets. Der Befund nennt die Anzahl je Pset. |

Die Dublettenprüfung läuft auch bei fehlendem Soll-Pset. Mehrere Verweise auf denselben Eintrag innerhalb eines Psets zählen als ein Vorkommen.

## IFC-Schema-Prüfung

IfcOpenShell verwendet das im Dateikopf deklarierte Schema, etwa `IFC4X3_ADD2`. Es prüft das gesamte Modell auf Pflichtangaben, Datentypen und die zulässige Anzahl von Einträgen. Das Fenster zeigt den Schemanamen und die Befunde an.

Kann die Datei nicht gelesen oder das deklarierte Schema nicht verarbeitet werden, zeigt das Fenster die Ursache an und behält die eingegebenen Regeln. Bei lesbaren Modellen mit Schema-Befunden führt die Anwendung die Attributprüfung ebenfalls aus.

## Ausgaben

Nach der Prüfung zeigt das Fenster den Prüfumfang, die Befundquote und eine Tabelle der Attributbefunde. Die Tabelle fasst Befunde nach Befundart, Soll-Pset, Attribut und Fundort zusammen und zeigt je Gruppe die Anzahl betroffener Objekte, ihren Anteil am Zählbereich und die Aufteilung nach IFC-Klassen. Die Gruppen sind nach der Anzahl betroffener Objekte absteigend sortiert. Die Schema-Befunde erscheinen im Reiter **Schema-Prüfung**. Der aktive Reiter ist farblich hervorgehoben, der andere bleibt grau und anklickbar.

Nach Abschluss erscheinen **CSV speichern** und **PDF speichern**. Beide Schaltflächen speichern die angezeigten Ergebnisse im Ordner `IFC_Pruefung/` neben der geprüften IFC-Datei. `ddmmYY` steht für das lokale Exportdatum mit zweistelligem Tag, Monat und Jahr. `IFC-Dateiname` ist der Dateiname des Modells ohne die Endung `.ifc`.

| Datei | Inhalt |
| --- | --- |
| `ddmmYY_QS_IFC-Dateiname.csv` | QS-Übersicht mit denselben Befundgruppen, Objektzahlen, Anteilen und IFC-Klassen wie im Fenster. |
| `ddmmYY_QS_Fehlerliste_IFC-Dateiname.csv` | Einzelbefunde der Attributprüfung mit STEP-ID, IFC-Klasse, Objektname und IFC-GlobalId. |
| `ddmmYY_QS_IFC-Dateiname.pdf` | Prüfbericht mit Kennzahlen, gruppierten Attributbefunden und Schema-Befunden. |

**CSV speichern** schreibt beide CSV-Dateien als Paar. Bei weiteren CSV-Exporten derselben Datei am selben Tag erhalten beide Dateinamen dieselbe laufende Nummer ab `_2`, beispielsweise `271126_QS_VST_V1_2.csv` und `271126_QS_Fehlerliste_VST_V1_2.csv`. PDF-Exporte werden separat nummeriert.

Die Befundquote ist der Anteil der Objekte mit mindestens einem Attributbefund an allen Objekten im Zählbereich. Dieser umfasst `IfcElement` und die zusätzlich für den Prüflauf zugeordneten Klassen. Pro Prüflauf zählt jedes betroffene Objekt für die Quote einmal. Ein Objekt zählt in jeder Befundgruppe, zu der es beiträgt, einmal.

## Architektur

- Das Fenster verwendet Tkinter und verwaltet Dateiauswahl und Regeleingabe. Beim Start über **Überprüfung starten** übergibt es den Dateipfad und die bestätigten Regeln an die Hintergrundaufgabe.
- Der Prüfkern verwendet Python 3.11 oder neuer und IfcOpenShell. Er verwendet die übergebenen Regeln als Eingabe, führt beide Prüfungen aus und liefert strukturierte Ergebnisse mit Modellbezug.
- Die Ergebnisansicht und die Exportfunktionen verwenden dieselben Prüfergebnisse. ReportLab erstellt den PDF-Bericht. Die Python-Abhängigkeiten lassen sich mit `python -m pip install -r requirements.txt` installieren.
