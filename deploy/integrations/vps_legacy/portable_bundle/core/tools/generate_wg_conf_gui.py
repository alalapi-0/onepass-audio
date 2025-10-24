#!/usr/bin/env python3
"""Tkinter UI for generating WireGuard client configurations."""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext

from generate_wg_conf import (
    ensure_output_path,
    render_configuration_from_files,
)


class WireGuardGeneratorUI:
    """A lightweight GUI for invoking the configuration generator."""

    def __init__(self, master: tk.Tk) -> None:
        self.master = master
        master.title("PrivateTunnel WireGuard Generator")
        master.geometry("720x420")

        default_schema = Path(__file__).resolve().parents[1] / "config-schema.json"

        self.schema_var = tk.StringVar(value=str(default_schema))
        self.input_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.overwrite_var = tk.BooleanVar(value=False)

        self._build_form()

    # ------------------------------------------------------------------
    def _build_form(self) -> None:
        padding = {"padx": 10, "pady": 6}

        schema_label = tk.Label(self.master, text="Schema 文件：")
        schema_label.grid(row=0, column=0, sticky="e", **padding)

        schema_entry = tk.Entry(self.master, textvariable=self.schema_var, width=60)
        schema_entry.grid(row=0, column=1, sticky="we", **padding)

        schema_button = tk.Button(self.master, text="浏览", command=self._browse_schema)
        schema_button.grid(row=0, column=2, **padding)

        input_label = tk.Label(self.master, text="配置 JSON：")
        input_label.grid(row=1, column=0, sticky="e", **padding)

        input_entry = tk.Entry(self.master, textvariable=self.input_var, width=60)
        input_entry.grid(row=1, column=1, sticky="we", **padding)

        input_button = tk.Button(self.master, text="浏览", command=self._browse_input)
        input_button.grid(row=1, column=2, **padding)

        output_label = tk.Label(self.master, text="输出文件：")
        output_label.grid(row=2, column=0, sticky="e", **padding)

        output_entry = tk.Entry(self.master, textvariable=self.output_var, width=60)
        output_entry.grid(row=2, column=1, sticky="we", **padding)

        output_button = tk.Button(self.master, text="浏览", command=self._browse_output)
        output_button.grid(row=2, column=2, **padding)

        overwrite_check = tk.Checkbutton(
            self.master,
            text="允许覆盖已存在的文件",
            variable=self.overwrite_var,
        )
        overwrite_check.grid(row=3, column=1, sticky="w", **padding)

        generate_button = tk.Button(
            self.master,
            text="生成 WireGuard 配置",
            command=self._generate_configuration,
            bg="#2563eb",
            fg="white",
        )
        generate_button.grid(row=4, column=1, sticky="we", **padding)

        log_label = tk.Label(self.master, text="运行日志：")
        log_label.grid(row=5, column=0, sticky="ne", **padding)

        self.log_widget = scrolledtext.ScrolledText(self.master, height=10)
        self.log_widget.grid(row=5, column=1, columnspan=2, sticky="nsew", **padding)
        self.log_widget.configure(state="disabled")

        for column in range(3):
            self.master.grid_columnconfigure(column, weight=1)
        self.master.grid_rowconfigure(5, weight=1)

    # ------------------------------------------------------------------
    def _browse_schema(self) -> None:
        file_path = filedialog.askopenfilename(
            title="选择 JSON Schema",
            filetypes=[("JSON 文件", "*.json"), ("所有文件", "*.*")],
        )
        if file_path:
            self.schema_var.set(file_path)

    def _browse_input(self) -> None:
        file_path = filedialog.askopenfilename(
            title="选择客户端配置 JSON",
            filetypes=[("JSON 文件", "*.json"), ("所有文件", "*.*")],
        )
        if file_path:
            self.input_var.set(file_path)

    def _browse_output(self) -> None:
        file_path = filedialog.asksaveasfilename(
            title="选择输出位置",
            defaultextension=".conf",
            filetypes=[("WireGuard 配置", "*.conf"), ("所有文件", "*.*")],
        )
        if file_path:
            self.output_var.set(file_path)

    # ------------------------------------------------------------------
    def _generate_configuration(self) -> None:
        schema_path = self._resolve_path(self.schema_var.get())
        input_path = self._resolve_path(self.input_var.get())
        output_path = self._resolve_path(self.output_var.get())

        if not schema_path or not input_path or not output_path:
            messagebox.showwarning("缺少参数", "请先选择 Schema、配置 JSON 和输出位置。")
            return

        try:
            ensure_output_path(output_path, self.overwrite_var.get())
            config_text = render_configuration_from_files(schema_path, input_path)
            output_path.write_text(config_text, encoding="utf-8")
        except FileExistsError as exc:
            messagebox.showerror("无法覆盖文件", str(exc))
            return
        except Exception as exc:  # pylint: disable=broad-except
            messagebox.showerror("生成失败", str(exc))
            self._append_log(f"❌ 生成失败：{exc}")
            return

        success_message = (
            f"WireGuard 配置已写入 {output_path}\n"
            "请在分发前妥善保护该文件。"
        )
        messagebox.showinfo("生成成功", success_message)
        self._append_log(f"✅ 生成成功：{output_path}")

    @staticmethod
    def _resolve_path(value: str) -> Path | None:
        value = value.strip()
        if not value:
            return None
        return Path(value).expanduser().resolve()

    def _append_log(self, message: str) -> None:
        self.log_widget.configure(state="normal")
        self.log_widget.insert(tk.END, message + "\n")
        self.log_widget.configure(state="disabled")
        self.log_widget.see(tk.END)


def main() -> None:
    try:
        root = tk.Tk()
    except tk.TclError as exc:  # pragma: no cover - requires GUI environment
        print(
            "无法启动图形界面：未检测到可用的显示环境 (DISPLAY)。\n"
            "请在桌面环境中运行，或者使用命令行工具：\n"
            "  python3 core/tools/generate_wg_conf.py --schema <schema> --in <input> --out <output> [--force]",
            flush=True,
        )
        raise SystemExit(1) from exc

    WireGuardGeneratorUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
