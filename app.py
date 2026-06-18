from __future__ import annotations

import os
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Dict, List, Optional

from iges_calc import (
    AnalysisResult,
    ThicknessPrice,
    analyze_iges_file,
    load_price_book,
    save_price_book,
)


APP_NAME = "IGES Cut Calculator"
APP_DIR = Path(__file__).resolve().parent


def settings_path() -> Path:
    if sys.platform.startswith("win"):
        appdata = os.environ.get("APPDATA")
        base_dir = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
        return base_dir / APP_NAME / "settings.json"
    return APP_DIR / "settings.json"


SETTINGS_PATH = settings_path()


class CutCalculatorApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("IGES Cut Calculator")
        self.geometry("1180x720")
        self.minsize(960, 600)

        self.currency, self.prices = load_price_book(SETTINGS_PATH)
        self.results: Dict[str, AnalysisResult] = {}
        self.selected_thickness = tk.StringVar()
        self.currency_var = tk.StringVar(value=self.currency)
        self.status_var = tk.StringVar(value="Ready")

        self._build_layout()
        self._refresh_price_controls()

    def _build_layout(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        toolbar = ttk.Frame(self, padding=(12, 10, 12, 6))
        toolbar.grid(row=0, column=0, sticky="ew")
        toolbar.columnconfigure(8, weight=1)

        ttk.Button(toolbar, text="Open IGES", command=self.open_files).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(toolbar, text="Remove", command=self.remove_selected).grid(row=0, column=1, padx=(0, 16))

        ttk.Label(toolbar, text="Thickness").grid(row=0, column=2, sticky="w")
        self.thickness_combo = ttk.Combobox(
            toolbar,
            textvariable=self.selected_thickness,
            width=14,
            state="readonly",
        )
        self.thickness_combo.grid(row=0, column=3, padx=(6, 16))
        self.thickness_combo.bind("<<ComboboxSelected>>", lambda _event: self.recalculate())

        ttk.Label(toolbar, text="Currency").grid(row=0, column=4, sticky="w")
        ttk.Entry(toolbar, textvariable=self.currency_var, width=10).grid(row=0, column=5, padx=(6, 16))
        ttk.Button(toolbar, text="Save Settings", command=self.save_settings).grid(row=0, column=6)

        body = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        body.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 8))

        left = ttk.Frame(body)
        right = ttk.Frame(body, padding=(12, 0, 0, 0))
        body.add(left, weight=4)
        body.add(right, weight=2)

        left.rowconfigure(0, weight=1)
        left.columnconfigure(0, weight=1)
        columns = (
            "file",
            "mode",
            "unit",
            "length_m",
            "pierces",
            "thickness",
            "price",
            "warnings",
        )
        self.result_table = ttk.Treeview(left, columns=columns, show="headings", selectmode="extended")
        headings = {
            "file": "File",
            "mode": "Mode",
            "unit": "Unit",
            "length_m": "Cut length, m",
            "pierces": "Pierces",
            "thickness": "Thickness",
            "price": "Price",
            "warnings": "Warnings",
        }
        widths = {
            "file": 230,
            "mode": 110,
            "unit": 80,
            "length_m": 120,
            "pierces": 80,
            "thickness": 100,
            "price": 110,
            "warnings": 280,
        }
        for column in columns:
            self.result_table.heading(column, text=headings[column])
            self.result_table.column(column, width=widths[column], anchor=tk.W)
        self.result_table.grid(row=0, column=0, sticky="nsew")

        result_scroll = ttk.Scrollbar(left, orient=tk.VERTICAL, command=self.result_table.yview)
        result_scroll.grid(row=0, column=1, sticky="ns")
        self.result_table.configure(yscrollcommand=result_scroll.set)

        right.columnconfigure(0, weight=1)
        ttk.Label(right, text="Prices", font=("", 13, "bold")).grid(row=0, column=0, sticky="w")

        price_columns = ("thickness", "cut_price", "pierce_price")
        self.price_table = ttk.Treeview(right, columns=price_columns, show="headings", height=9)
        for column, heading, width in (
            ("thickness", "Thickness, mm", 110),
            ("cut_price", "Per meter", 110),
            ("pierce_price", "Per pierce", 110),
        ):
            self.price_table.heading(column, text=heading)
            self.price_table.column(column, width=width, anchor=tk.W)
        self.price_table.grid(row=1, column=0, sticky="ew", pady=(8, 8))
        self.price_table.bind("<<TreeviewSelect>>", lambda _event: self._load_selected_price())

        form = ttk.Frame(right)
        form.grid(row=2, column=0, sticky="ew")
        for column in range(2):
            form.columnconfigure(column, weight=1)

        self.thickness_entry = self._entry_row(form, "Thickness, mm", 0)
        self.cut_price_entry = self._entry_row(form, "Price per meter", 1)
        self.pierce_price_entry = self._entry_row(form, "Price per pierce", 2)

        price_buttons = ttk.Frame(right)
        price_buttons.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        ttk.Button(price_buttons, text="Add / Update", command=self.add_or_update_price).grid(
            row=0, column=0, padx=(0, 8)
        )
        ttk.Button(price_buttons, text="Delete", command=self.delete_price).grid(row=0, column=1)

        details = ttk.LabelFrame(right, text="Selected file")
        details.grid(row=4, column=0, sticky="nsew", pady=(18, 0))
        right.rowconfigure(4, weight=1)
        details.rowconfigure(0, weight=1)
        details.columnconfigure(0, weight=1)
        self.details_text = tk.Text(details, height=10, wrap="word", relief="flat")
        self.details_text.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        self.result_table.bind("<<TreeviewSelect>>", lambda _event: self._show_selected_details())

        status = ttk.Label(self, textvariable=self.status_var, anchor=tk.W, padding=(12, 0, 12, 8))
        status.grid(row=2, column=0, sticky="ew")

    def _entry_row(self, parent: ttk.Frame, label: str, row: int) -> ttk.Entry:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=(0, 6))
        entry = ttk.Entry(parent)
        entry.grid(row=row, column=1, sticky="ew", pady=(0, 6))
        return entry

    def open_files(self) -> None:
        filenames = filedialog.askopenfilenames(
            title="Open IGES files",
            filetypes=(("IGES files", "*.igs *.iges"), ("All files", "*.*")),
        )
        if not filenames:
            return

        loaded = 0
        for filename in filenames:
            try:
                result = analyze_iges_file(filename)
            except Exception as exc:
                messagebox.showerror("IGES error", f"{Path(filename).name}\n\n{exc}")
                continue
            self.results[str(Path(filename))] = result
            loaded += 1

        self.recalculate()
        self.status_var.set(f"Loaded {loaded} file(s)")

    def remove_selected(self) -> None:
        for item in self.result_table.selection():
            self.results.pop(item, None)
        self.recalculate()

    def add_or_update_price(self) -> None:
        try:
            price = ThicknessPrice(
                float(self.thickness_entry.get().replace(",", ".")),
                float(self.cut_price_entry.get().replace(",", ".")),
                float(self.pierce_price_entry.get().replace(",", ".")),
            )
        except ValueError:
            messagebox.showerror("Price error", "Enter numeric values for all price fields.")
            return

        self.prices = [item for item in self.prices if item.thickness_mm != price.thickness_mm]
        self.prices.append(price)
        self._refresh_price_controls(select=price)
        self.recalculate()

    def delete_price(self) -> None:
        selected = self._selected_price()
        if selected is None:
            return
        self.prices = [item for item in self.prices if item.thickness_mm != selected.thickness_mm]
        self._refresh_price_controls()
        self.recalculate()

    def save_settings(self) -> None:
        self.currency = self.currency_var.get().strip() or "RUB"
        save_price_book(SETTINGS_PATH, self.currency, self.prices)
        self.status_var.set(f"Settings saved: {SETTINGS_PATH}")
        self.recalculate()

    def recalculate(self) -> None:
        selected_price = self._price_by_label(self.selected_thickness.get())
        self.result_table.delete(*self.result_table.get_children())
        for result in self.results.values():
            if selected_price is None:
                thickness = "-"
                price_text = "-"
            else:
                thickness = selected_price.label()
                price_text = f"{selected_price.calculate(result):.2f} {self.currency_var.get().strip()}"

            warning_count = len(result.warnings)
            warning_text = "" if warning_count == 0 else f"{warning_count} warning(s)"
            self.result_table.insert(
                "",
                tk.END,
                iid=str(result.path),
                values=(
                    result.path.name,
                    result.backend,
                    result.unit_name,
                    f"{result.cut_length_m:.3f}",
                    result.pierces,
                    thickness,
                    price_text,
                    warning_text,
                ),
            )
        self._show_selected_details()

    def _refresh_price_controls(self, select: Optional[ThicknessPrice] = None) -> None:
        self.prices = sorted(self.prices, key=lambda item: item.thickness_mm)
        self.price_table.delete(*self.price_table.get_children())
        for price in self.prices:
            self.price_table.insert(
                "",
                tk.END,
                values=(
                    f"{price.thickness_mm:g}",
                    f"{price.cut_price_per_meter:g}",
                    f"{price.pierce_price:g}",
                ),
            )

        labels = [price.label() for price in self.prices]
        self.thickness_combo.configure(values=labels)
        if select is not None:
            self.selected_thickness.set(select.label())
        elif labels and self.selected_thickness.get() not in labels:
            self.selected_thickness.set(labels[0])

    def _load_selected_price(self) -> None:
        selected = self._selected_price()
        if selected is None:
            return
        self._set_entry(self.thickness_entry, f"{selected.thickness_mm:g}")
        self._set_entry(self.cut_price_entry, f"{selected.cut_price_per_meter:g}")
        self._set_entry(self.pierce_price_entry, f"{selected.pierce_price:g}")

    def _show_selected_details(self) -> None:
        self.details_text.configure(state="normal")
        self.details_text.delete("1.0", tk.END)
        selected = self.result_table.selection()
        if not selected:
            self.details_text.configure(state="disabled")
            return

        result = self.results.get(selected[0])
        if result is None:
            self.details_text.configure(state="disabled")
            return

        lines: List[str] = [
            f"File: {result.path}",
            f"Mode: {result.backend}",
            f"Calculation: {result.calculation_mode}",
            f"Unit: {result.unit_name}",
            f"Cut length: {result.cut_length_m:.4f} m",
            f"Pierces: {result.pierces}",
            f"Cut elements used: {len(result.curves)}",
        ]
        if result.warnings:
            lines.append("")
            lines.append("Warnings:")
            lines.extend(f"- {warning}" for warning in result.warnings)
        self.details_text.insert("1.0", "\n".join(lines))
        self.details_text.configure(state="disabled")

    def _selected_price(self) -> Optional[ThicknessPrice]:
        selected = self.price_table.selection()
        if not selected:
            return None
        values = self.price_table.item(selected[0], "values")
        if not values:
            return None
        thickness = float(str(values[0]).replace(",", "."))
        return next((price for price in self.prices if price.thickness_mm == thickness), None)

    def _price_by_label(self, label: str) -> Optional[ThicknessPrice]:
        return next((price for price in self.prices if price.label() == label), None)

    @staticmethod
    def _set_entry(entry: ttk.Entry, value: str) -> None:
        entry.delete(0, tk.END)
        entry.insert(0, value)


def main() -> None:
    app = CutCalculatorApp()
    app.mainloop()


if __name__ == "__main__":
    main()
