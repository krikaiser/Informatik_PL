"""Dropdown zur Mehrfachauswahl von IFC-Klassen."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk


class ClassPicker(ttk.Button):
    def __init__(self, parent, confirm):
        super().__init__(parent, command=self.open_menu)
        self.confirm = confirm
        self.classes: tuple[str, ...] = ()
        self.selected: set[str] = set()
        self.menu: tk.Toplevel | None = None
        self.update_label()

    def set_classes(self, classes: tuple[str, ...], selected=()) -> None:
        self.close_menu()
        self.classes = classes
        self.selected = set(classes).intersection(selected)
        self.update_label()

    def selection(self) -> tuple[str, ...]:
        return tuple(name for name in self.classes if name in self.selected)

    def update_label(self) -> None:
        if not self.selected:
            label = "IFC-Klassen auswählen"
        elif len(self.selected) == len(self.classes):
            label = f"Alle ({len(self.classes)})"
        elif len(self.selected) == 1:
            label = next(iter(self.selected))
        else:
            label = f"{len(self.selected)} Klassen ausgewählt"
        self.configure(text=f"{label}  ▾")

    def open_menu(self) -> None:
        if self.menu is not None:
            self.close_menu()
            return
        menu = self.menu = tk.Toplevel(self)
        menu.withdraw()
        menu.overrideredirect(True)
        menu.transient(self.winfo_toplevel())
        panel = ttk.Frame(menu, padding=8, relief="solid", borderwidth=1)
        panel.pack(fill="both", expand=True)
        ttk.Label(panel, text="Suche ab 3 Zeichen").pack(anchor="w")
        query = tk.StringVar(menu)
        search = ttk.Entry(panel, textvariable=query)
        search.pack(fill="x", pady=(3, 6))
        all_checked = tk.BooleanVar(menu, value=len(self.selected) == len(self.classes))
        checks = {name: tk.BooleanVar(menu, value=name in self.selected) for name in self.classes}

        def toggle_all() -> None:
            for check in checks.values():
                check.set(all_checked.get())
            sync_selection()

        def sync_selection() -> None:
            self.selected = {name for name, check in checks.items() if check.get()}
            all_checked.set(len(self.selected) == len(self.classes))
            self.update_label()

        ttk.Checkbutton(panel, text="Alle", variable=all_checked, command=toggle_all).pack(anchor="w")
        body = ttk.Frame(panel)
        body.pack(fill="both", expand=True, pady=(4, 0))
        canvas = tk.Canvas(body, highlightthickness=0, background="white", height=220)
        scroll = ttk.Scrollbar(body, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        options = ttk.Frame(canvas)
        window = canvas.create_window((0, 0), window=options, anchor="nw")
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window, width=event.width))
        options.bind("<Configure>", lambda _: canvas.configure(scrollregion=canvas.bbox("all")))

        def filter_classes(*_args) -> None:
            for child in options.winfo_children():
                child.destroy()
            term = query.get().strip().casefold()
            visible = [name for name in self.classes if len(term) < 3 or term in name.casefold()]
            for name in visible:
                ttk.Checkbutton(options, text=name, variable=checks[name], command=sync_selection).pack(anchor="w")
            if not visible:
                ttk.Label(options, text="Keine passenden IFC-Klassen").pack(anchor="w")
            canvas.yview_moveto(0)

        def confirm_from_menu(event) -> str:
            self.close_menu()
            return self.confirm(event)

        def close_outside(event) -> None:
            x, y = menu.winfo_rootx(), menu.winfo_rooty()
            if not (x <= event.x_root < x + menu.winfo_width() and y <= event.y_root < y + menu.winfo_height()):
                self.close_menu()

        query.trace_add("write", filter_classes)
        filter_classes()
        menu.bind("<Button-1>", close_outside)
        menu.bind("<Escape>", lambda _: self.close_menu())
        menu.bind("<Return>", confirm_from_menu)
        menu.bind("<KP_Enter>", confirm_from_menu)
        menu.bind("<MouseWheel>", lambda event: canvas.yview_scroll(-event.delta // 120, "units"))
        menu.update_idletasks()
        width = max(320, self.winfo_width())
        height = menu.winfo_reqheight()
        x = self.winfo_rootx()
        y = self.winfo_rooty() + self.winfo_height()
        if y + height > self.winfo_screenheight():
            y = self.winfo_rooty() - height
        menu.geometry(f"{width}x{height}{x:+}{y:+}")

        def focus_menu(event) -> None:
            if event.widget is menu:
                menu.grab_set()
                search.focus_force()

        menu.bind("<Map>", focus_menu)
        menu.deiconify()

    def close_menu(self) -> None:
        if self.menu is not None:
            menu, self.menu = self.menu, None
            menu.destroy()
