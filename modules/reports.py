import io
from datetime import datetime
import pandas as pd
from fpdf import FPDF


def _sanear_texto(texto: any) -> str:
    """Normaliza o codifica de forma segura textos para evitar errores de codificación."""
    if texto is None or pd.isna(texto):
        return ""
    texto_str = str(texto)
    # Reemplazar caracteres especiales que puedan dar problemas si no existen en latin-1
    return (
        texto_str.replace("≥", ">=")
        .replace("≤", "<=")
        .replace("•", "-")
        .replace("—", "-")
        .replace("–", "-")
    )


class ReportePDF(FPDF):
    def __init__(self, operador: str, fecha_hora: str):
        super().__init__(orientation="L", unit="mm", format="A4")
        self.operador = operador
        self.fecha_hora = fecha_hora
        self.set_auto_page_break(auto=True, margin=15)

    def header(self):
        # Título y encabezado institucional
        self.set_fill_color(16, 44, 87)  # Azul corporativo oscuro #102C57
        self.rect(0, 0, 297, 18, style="F")

        self.set_font("helvetica", "B", 13)
        self.set_text_color(255, 255, 255)
        self.set_xy(10, 4)
        self.cell(
            180,
            10,
            text=_sanear_texto("SISTEMA INTEGRAL A3 STOCK - REPORTE DE ACTIVOS"),
            align="L",
        )

        self.set_font("helvetica", "", 9)
        self.set_xy(190, 4)
        info_header = f"Operador: {_sanear_texto(self.operador)} | Fecha: {self.fecha_hora}"
        self.cell(97, 10, text=info_header, align="R")

        self.set_text_color(0, 0, 0)
        self.set_y(22)

    def footer(self):
        self.set_y(-12)
        self.set_font("helvetica", "I", 8)
        self.set_text_color(120, 120, 120)
        # Línea divisoria
        self.set_draw_color(200, 200, 200)
        self.line(10, self.get_y(), 287, self.get_y())
        self.set_y(-10)
        self.cell(
            0,
            6,
            text=_sanear_texto(
                f"A3 Stock - Informe Confidencial | Página {self.page_no()} de {{nb}}"
            ),
            align="C",
        )


def generar_excel_inventario(df: pd.DataFrame, kpis: dict, operador: str) -> bytes:
    """Genera un archivo Excel (.xlsx) en memoria con las hojas Resumen_Ejecutivo e Inventario_Detallado."""
    output = io.BytesIO()
    fecha_actual = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    df_seguro = df.copy() if df is not None and not df.empty else pd.DataFrame()

    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        workbook = writer.book

        # Estilos y formatos
        fmt_titulo = workbook.add_format({
            "bold": True,
            "font_size": 16,
            "font_color": "#0B2447",
            "bottom": 2,
            "bottom_color": "#19376D",
        })
        fmt_meta = workbook.add_format({
            "font_size": 10,
            "font_color": "#475569",
            "italic": True,
        })
        fmt_seccion = workbook.add_format({
            "bold": True,
            "font_size": 12,
            "font_color": "#0F172A",
            "bg_color": "#E2E8F0",
            "border": 1,
            "border_color": "#CBD5E1",
        })
        fmt_th = workbook.add_format({
            "bold": True,
            "font_size": 10,
            "font_color": "#FFFFFF",
            "bg_color": "#102C57",
            "border": 1,
            "border_color": "#0B2447",
            "align": "center",
            "valign": "vcenter",
        })
        fmt_cell = workbook.add_format({
            "font_size": 10,
            "border": 1,
            "border_color": "#E2E8F0",
            "valign": "vcenter",
        })
        fmt_cell_center = workbook.add_format({
            "font_size": 10,
            "border": 1,
            "border_color": "#E2E8F0",
            "align": "center",
            "valign": "vcenter",
        })
        fmt_cell_num = workbook.add_format({
            "font_size": 10,
            "border": 1,
            "border_color": "#E2E8F0",
            "align": "right",
            "valign": "vcenter",
            "num_format": "#,##0",
        })
        fmt_pct = workbook.add_format({
            "font_size": 10,
            "border": 1,
            "border_color": "#E2E8F0",
            "align": "right",
            "valign": "vcenter",
            "num_format": "0.0%",
        })

        # --- Hoja 1: Resumen_Ejecutivo ---
        ws_resumen = workbook.add_worksheet("Resumen_Ejecutivo")
        ws_resumen.hide_gridlines(0)

        # Encabezado institucional
        ws_resumen.write("B2", "A3 Stock - Reporte Ejecutivo de Activos", fmt_titulo)
        ws_resumen.write("B3", f"Generado: {fecha_actual} | Operador: {operador}", fmt_meta)

        # Tabla KPIs
        ws_resumen.write("B5", "Métricas Clave del Inventario (KPIs)", fmt_seccion)
        ws_resumen.write("C5", "", fmt_seccion)

        headers_kpi = ["Métrica / Indicador", "Valor"]
        for col_idx, h in enumerate(headers_kpi):
            ws_resumen.write(5, 1 + col_idx, h, fmt_th)

        total_act = kpis.get("total_activos", 0) if kpis else 0
        avg_vida = kpis.get("avg_vida_util_pct", 0.0) if kpis else 0.0
        desg_crit = kpis.get("desgaste_critico", 0) if kpis else 0
        expirados = kpis.get("expirados", 0) if kpis else 0

        filas_kpi = [
            ("Total de Activos Registrados", total_act, fmt_cell_num),
            ("Promedio de Vida Útil Consumida", avg_vida / 100.0, fmt_pct),
            ("Activos en Desgaste Crítico (>=80%)", desg_crit, fmt_cell_num),
            ("Activos Expirados (>=100%)", expirados, fmt_cell_num),
        ]

        curr_row = 6
        for label, val, formato in filas_kpi:
            ws_resumen.write(curr_row, 1, label, fmt_cell)
            ws_resumen.write(curr_row, 2, val, formato)
            curr_row += 1

        curr_row += 2

        # Resumen por Sede
        ws_resumen.write(curr_row, 1, "Distribución de Activos por Sede", fmt_seccion)
        ws_resumen.write(curr_row, 2, "", fmt_seccion)
        curr_row += 1

        ws_resumen.write(curr_row, 1, "Sede", fmt_th)
        ws_resumen.write(curr_row, 2, "Cantidad de Activos", fmt_th)
        curr_row += 1

        if not df_seguro.empty and "sd" in df_seguro.columns:
            resumen_sede = df_seguro["sd"].fillna("Sin Sede").value_counts().reset_index()
            resumen_sede.columns = ["Sede", "Cantidad"]
            for _, r in resumen_sede.iterrows():
                ws_resumen.write(curr_row, 1, str(r["Sede"]), fmt_cell)
                ws_resumen.write(curr_row, 2, int(r["Cantidad"]), fmt_cell_num)
                curr_row += 1
        else:
            ws_resumen.write(curr_row, 1, "Sin registros", fmt_cell)
            ws_resumen.write(curr_row, 2, 0, fmt_cell_num)
            curr_row += 1

        curr_row += 2

        # Resumen por Categoría
        ws_resumen.write(curr_row, 1, "Distribución de Activos por Categoría", fmt_seccion)
        ws_resumen.write(curr_row, 2, "", fmt_seccion)
        curr_row += 1

        ws_resumen.write(curr_row, 1, "Categoría", fmt_th)
        ws_resumen.write(curr_row, 2, "Cantidad de Activos", fmt_th)
        curr_row += 1

        if not df_seguro.empty and "cat" in df_seguro.columns:
            resumen_cat = df_seguro["cat"].fillna("Sin Categoría").value_counts().reset_index()
            resumen_cat.columns = ["Categoría", "Cantidad"]
            for _, r in resumen_cat.iterrows():
                ws_resumen.write(curr_row, 1, str(r["Categoría"]), fmt_cell)
                ws_resumen.write(curr_row, 2, int(r["Cantidad"]), fmt_cell_num)
                curr_row += 1
        else:
            ws_resumen.write(curr_row, 1, "Sin registros", fmt_cell)
            ws_resumen.write(curr_row, 2, 0, fmt_cell_num)
            curr_row += 1

        ws_resumen.set_column(0, 0, 4)
        ws_resumen.set_column(1, 1, 38)
        ws_resumen.set_column(2, 2, 24)

        # --- Hoja 2: Inventario_Detallado ---
        ws_detalle = workbook.add_worksheet("Inventario_Detallado")
        ws_detalle.hide_gridlines(0)

        # Columnas solicitadas: id, nombre, cat -> Categoría, sd -> Sede, marca, serial, pct_vida -> Vida Útil %, estado
        mapeo_columnas = [
            ("id", "ID", fmt_cell_center, 10),
            ("nombre", "Producto", fmt_cell, 30),
            ("cat", "Categoría", fmt_cell, 20),
            ("sd", "Sede", fmt_cell, 20),
            ("marca", "Marca", fmt_cell, 18),
            ("serial", "Serial", fmt_cell, 22),
            ("pct_vida", "Vida Útil %", fmt_pct, 16),
            ("estado", "Estado", fmt_cell_center, 18),
        ]

        # Formato encabezado tabla detallada (Azul oscuro / Cian)
        fmt_th_det = workbook.add_format({
            "bold": True,
            "font_size": 11,
            "font_color": "#FFFFFF",
            "bg_color": "#0B2447",
            "border": 1,
            "border_color": "#19376D",
            "align": "center",
            "valign": "vcenter",
        })

        for col_idx, (_, header_title, _, col_width) in enumerate(mapeo_columnas):
            ws_detalle.write(0, col_idx, header_title, fmt_th_det)
            ws_detalle.set_column(col_idx, col_idx, col_width)

        fila_actual = 1
        if not df_seguro.empty:
            for _, fila in df_seguro.iterrows():
                for col_idx, (col_orig, _, formato_celda, _) in enumerate(mapeo_columnas):
                    val = fila.get(col_orig, "")
                    if col_orig == "pct_vida":
                        try:
                            val_num = float(val) / 100.0 if pd.notna(val) else 0.0
                            ws_detalle.write(fila_actual, col_idx, val_num, formato_celda)
                        except (ValueError, TypeError):
                            ws_detalle.write(fila_actual, col_idx, 0.0, formato_celda)
                    elif col_orig == "id":
                        try:
                            ws_detalle.write(fila_actual, col_idx, int(val), formato_celda)
                        except (ValueError, TypeError):
                            ws_detalle.write(fila_actual, col_idx, str(val), formato_celda)
                    else:
                        ws_detalle.write(
                            fila_actual,
                            col_idx,
                            "" if pd.isna(val) else str(val),
                            formato_celda,
                        )
                fila_actual += 1

            # Activar autofiltro en la tabla detallada
            num_cols = len(mapeo_columnas) - 1
            ws_detalle.autofilter(0, 0, max(fila_actual - 1, 1), num_cols)
        else:
            ws_detalle.write(1, 0, "No hay activos disponibles", fmt_cell)
            ws_detalle.autofilter(0, 0, 1, len(mapeo_columnas) - 1)

    output.seek(0)
    return output.getvalue()


def generar_pdf_inventario(df: pd.DataFrame, kpis: dict, operador: str) -> bytes:
    """Genera un archivo PDF (.pdf) en memoria con orientación horizontal (A4) y resumen de inventario."""
    fecha_actual = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    pdf = ReportePDF(operador=operador, fecha_hora=fecha_actual)
    pdf.alias_nb_pages()
    pdf.add_page()

    # --- Bloque Resumen de KPIs ---
    total_act = kpis.get("total_activos", 0) if kpis else 0
    avg_vida = kpis.get("avg_vida_util_pct", 0.0) if kpis else 0.0
    desg_crit = kpis.get("desgaste_critico", 0) if kpis else 0
    expirados = kpis.get("expirados", 0) if kpis else 0

    kpi_cards = [
        ("Total Activos", f"{total_act}", (235, 243, 250), (16, 44, 87)),
        ("Vida Útil Media", f"{avg_vida:.1f}%", (235, 248, 245), (10, 80, 60)),
        ("Desgaste Crítico", f"{desg_crit}", (255, 247, 237), (180, 83, 9)),
        ("Expirados", f"{expirados}", (254, 242, 242), (185, 28, 28)),
    ]

    card_w = 66.0
    card_h = 16.0
    start_x = 10.0
    y_kpis = pdf.get_y() + 2

    for i, (titulo_kpi, valor_kpi, bg_rgb, text_rgb) in enumerate(kpi_cards):
        x = start_x + (i * (card_w + 4.0))
        pdf.set_xy(x, y_kpis)
        pdf.set_fill_color(*bg_rgb)
        pdf.set_draw_color(210, 220, 230)
        pdf.rect(x, y_kpis, card_w, card_h, style="DF")

        # Texto Título KPI
        pdf.set_xy(x, y_kpis + 2)
        pdf.set_font("helvetica", "B", 8)
        pdf.set_text_color(100, 116, 139)
        pdf.cell(card_w, 4, text=_sanear_texto(titulo_kpi.upper()), align="C")

        # Texto Valor KPI
        pdf.set_xy(x, y_kpis + 7)
        pdf.set_font("helvetica", "B", 13)
        pdf.set_text_color(*text_rgb)
        pdf.cell(card_w, 7, text=_sanear_texto(valor_kpi), align="C")

    pdf.set_y(y_kpis + card_h + 6)

    # --- Título de la Sección de Tabla ---
    pdf.set_font("helvetica", "B", 11)
    pdf.set_text_color(16, 44, 87)
    pdf.cell(0, 7, text=_sanear_texto("LISTADO DETALLADO DE ACTIVOS"), align="L", new_x="LMARGIN", new_y="NEXT")

    # --- Definición de Columnas de la Tabla ---
    # Total ancho disponible en A4 horizontal: 297 - 20 (márgenes) = 277 mm
    cols = [
        {"key": "nombre", "label": "Producto", "width": 62, "align": "L"},
        {"key": "cat", "label": "Categoría", "width": 38, "align": "L"},
        {"key": "sd", "label": "Sede", "width": 38, "align": "L"},
        {"key": "marca", "label": "Marca", "width": 34, "align": "L"},
        {"key": "serial", "label": "Serial", "width": 45, "align": "L"},
        {"key": "pct_vida", "label": "Vida Útil %", "width": 28, "align": "R"},
        {"key": "estado", "label": "Estado", "width": 32, "align": "C"},
    ]

    # Encabezados de la tabla
    def imprimir_encabezado_tabla():
        pdf.set_fill_color(16, 44, 87)
        pdf.set_text_color(255, 255, 255)
        pdf.set_font("helvetica", "B", 9)
        for c in cols:
            pdf.cell(c["width"], 8, text=_sanear_texto(c["label"]), border=1, align="C", fill=True)
        pdf.ln()

    imprimir_encabezado_tabla()

    df_seguro = df.copy() if df is not None and not df.empty else pd.DataFrame()

    if df_seguro.empty:
        pdf.set_font("helvetica", "I", 10)
        pdf.set_text_color(100, 100, 100)
        pdf.cell(277, 10, text=_sanear_texto("No se encontraron activos para los criterios seleccionados."), border=1, align="C")
    else:
        pdf.set_font("helvetica", "", 8.5)
        fila_num = 0

        for _, row in df_seguro.iterrows():
            # Salto de página preventivo para mantener la tabla limpia
            if pdf.get_y() > 180:
                pdf.add_page()
                imprimir_encabezado_tabla()
                pdf.set_font("helvetica", "", 8.5)

            # Fondo alternado en filas
            if fila_num % 2 == 0:
                pdf.set_fill_color(248, 250, 252)
            else:
                pdf.set_fill_color(255, 255, 255)

            pdf.set_text_color(30, 41, 59)

            for c in cols:
                valor = row.get(c["key"], "")
                if c["key"] == "pct_vida":
                    try:
                        v_num = float(valor) if pd.notna(valor) else 0.0
                        val_str = f"{v_num:.1f}%"
                    except (ValueError, TypeError):
                        val_str = "0.0%"
                else:
                    val_str = "" if pd.isna(valor) else str(valor)

                # Truncar texto si excede el ancho visual aproximado para evitar overflow
                max_chars = int(c["width"] * 0.48)
                if len(val_str) > max_chars and c["align"] != "R":
                    val_str = val_str[: max_chars - 2] + ".."

                pdf.cell(
                    c["width"],
                    7,
                    text=_sanear_texto(val_str),
                    border=1,
                    align=c["align"],
                    fill=True,
                )
            pdf.ln()
            fila_num += 1

    return bytes(pdf.output())
