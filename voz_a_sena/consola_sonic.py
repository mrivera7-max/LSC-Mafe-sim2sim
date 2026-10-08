"""
Consola integrada: muestra la salida del simulador / deploy SONIC y permite enviarles teclas
(Enter, O = paro de emergencia, Ctrl+C) sin abrir terminales externas.
"""

import tkinter as tk
from tkinter import ttk


class ConsolaSonic(tk.Frame):
    """Panel (cabe en una pestaña) con la salida de los procesos que lanzó la app."""
    REFRESCO_MS = 300
    MAX_CARACTERES = 30_000

    def __init__(self, master, lanzador, clave_inicial: str = "deploy_sim"):
        super().__init__(master, bg="#0d1117")
        self.lanzador = lanzador

        arriba = tk.Frame(self, bg="#0d1117")
        arriba.pack(fill="x", padx=8, pady=(8, 4))
        tk.Label(arriba, text="Proceso:", bg="#0d1117", fg="#94a3b8").pack(side="left")
        self._claves = list(lanzador.NOMBRES)
        self._var = tk.StringVar(value=lanzador.NOMBRES.get(clave_inicial, clave_inicial))
        self._combo = ttk.Combobox(arriba, state="readonly", width=26, textvariable=self._var,
                                   values=[lanzador.NOMBRES[k] for k in self._claves])
        self._combo.pack(side="left", padx=6)
        self._lbl_estado = tk.Label(arriba, text="", bg="#0d1117", fg="#94a3b8")
        self._lbl_estado.pack(side="left", padx=8)

        self._texto = tk.Text(self, bg="#010409", fg="#c9d1d9", font=("Consolas", 9),
                              wrap="char", state="disabled", relief="flat")
        sb = tk.Scrollbar(self, command=self._texto.yview)
        self._texto.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y", padx=(0, 8), pady=4)
        self._texto.pack(fill="both", expand=True, padx=(8, 0), pady=4)

        abajo = tk.Frame(self, bg="#0d1117")
        abajo.pack(fill="x", padx=8, pady=(4, 8))
        self._entrada = tk.Entry(abajo, bg="#161b22", fg="#e6edf3", insertbackground="#e6edf3",
                                 relief="flat", font=("Consolas", 10))
        self._entrada.pack(side="left", fill="x", expand=True, ipady=4)
        self._entrada.bind("<Return>", lambda _e: self._enviar_linea())
        for txt, cmd, bg, fg in (
                ("Enviar", self._enviar_linea, "#2563eb", "white"),
                ("↵ Enter", lambda: self._enviar("\n"), "#475569", "white"),
                ("O  Paro de emergencia", lambda: self._enviar("o"), "#dc2626", "white"),
                ("Ctrl+C", lambda: self._enviar("\x03"), "#475569", "white")):
            tk.Button(abajo, text=txt, command=cmd, bg=bg, fg=fg, relief="flat",
                      font=("Segoe UI", 9, "bold"), padx=8, cursor="hand2").pack(side="left", padx=(6, 0))

        self._ultimo = None
        self._refrescar()

    # ── helpers ──────────────────────────────────────────────────────
    def clave(self) -> str:
        nombre = self._var.get()
        for k in self._claves:
            if self.lanzador.NOMBRES[k] == nombre:
                return k
        return self._claves[0]

    def seleccionar(self, clave: str):
        """Elige qué proceso se ve (MuJoCo, deploy sim o deploy real)."""
        self._var.set(self.lanzador.NOMBRES.get(clave, clave))
        self._ultimo = None

    def _enviar(self, texto: str):
        if not self.lanzador.enviar(self.clave(), texto):
            self._lbl_estado.configure(text="(ese proceso no está en marcha)", fg="#fca5a5")

    def _enviar_linea(self):
        self._enviar(self._entrada.get() + "\n")
        self._entrada.delete(0, "end")

    def _refrescar(self):
        try:
            if not self.winfo_exists():
                return
        except tk.TclError:
            return
        k = self.clave()
        salida = self.lanzador.salida(k)[-self.MAX_CARACTERES:]
        if salida != self._ultimo:
            self._ultimo = salida
            pegado = self._texto.yview()[1] >= 0.999
            self._texto.configure(state="normal")
            self._texto.delete("1.0", "end")
            self._texto.insert("end", salida)
            self._texto.configure(state="disabled")
            if pegado:
                self._texto.see("end")
        if self.lanzador.proceso_vivo(k):
            self._lbl_estado.configure(text="● en marcha", fg="#86efac")
        else:
            self._lbl_estado.configure(text="○ detenido / sin iniciar", fg="#94a3b8")
        self.after(self.REFRESCO_MS, self._refrescar)
