# -*- coding: utf-8 -*-
"""
Core logic extracted from reto_cripto_1_2.py (Gradio version) for Streamlit usage.
Includes: config, Fernet helpers, password hashing, CSV I/O, study list, and core ops.
"""

import re, secrets
import os, json, base64, hashlib, time
from datetime import datetime, date
import pandas as pd
from cryptography.fernet import Fernet
from pathlib import Path
from io import BytesIO
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader


# -------------------------
# Config / archivos
# -------------------------
CSV_PATH   = "solicitudes_lis.csv"
XLSX_PATH  = "solicitudes_lis.xlsx"
KEY_PATH   = "fernet.key"   # demo; en producción usar bóveda/entorno

# -------------------------
# Config editable para LABZA (lab + doctor)
# -------------------------

BASE_DIR = Path(__file__).resolve().parent
LOGO_PATH = BASE_DIR / "logo.png"           
CONFIG_PATH = BASE_DIR / "config_labza.json" 

DEFAULT_LAB_INFO = {
    "nombre": "LABZA | Laboratorio de Análisis Clínicos",
    "direccion": "",
    "telefono": "",
    "correo": "",
}

DEFAULT_DOCTOR_INFO = {
    "nombre": "",
    "cedula": "",
    "especialidad": "",
}

def load_labza_config():
    lab_info = DEFAULT_LAB_INFO.copy()
    doctor_info = DEFAULT_DOCTOR_INFO.copy()

    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            lab_info.update(data.get("lab_info", {}))
            doctor_info.update(data.get("doctor_info", {}))
        except Exception:
            pass
    return {"lab_info": lab_info, "doctor_info": doctor_info}

def save_labza_config(lab_info: dict, doctor_info: dict):
    data = {
        "lab_info": lab_info,
        "doctor_info": doctor_info,
    }
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return True

_config = load_labza_config()
LAB_INFO = _config["lab_info"]
DOCTOR_INFO = _config["doctor_info"]


# -------------------------
# Llave simétrica (Fernet)
# -------------------------
def load_or_create_key():
    # 1) If env var FERNET_KEY is set, use it (base64)
    env_key = os.getenv("FERNET_KEY")
    if env_key:
        try:
            return env_key.encode()
        except Exception:
            pass
    # 2) Else use local file (on-prem)
    if os.path.exists(KEY_PATH):
        return open(KEY_PATH, "rb").read()
    key = Fernet.generate_key()
    with open(KEY_PATH, "wb") as f:
        f.write(key)
    return key

FERNET = Fernet(load_or_create_key())

def enc(s: str) -> str:
    if s is None: s = ""
    return FERNET.encrypt(s.encode()).decode()

def dec(s: str) -> str:
    if not isinstance(s, str) or not s:
        return ""
    try:
        return FERNET.decrypt(s.encode()).decode()
    except Exception:
        return ""  # tolerante a valores antiguos/no cifrados

# -------------------------
# Hash de contraseñas (PBKDF2 — demo)
# -------------------------
def pbkdf2_hash(password: str, salt: bytes) -> str:
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)
    return base64.b64encode(dk).decode()

def make_user(password: str, role: str):
    import os, base64
    salt = os.urandom(16)
    return {
        "salt": base64.b64encode(salt).decode(),
        "hash": pbkdf2_hash(password, salt),
        "role": role
    }

def verify_password(password: str, salt_b64: str, hash_b64: str) -> bool:
    import base64
    salt = base64.b64decode(salt_b64.encode())
    return pbkdf2_hash(password, salt) == hash_b64

# -------------------------
# Usuarios persistentes en JSON
# -------------------------

USERS_FILE = Path(__file__).with_name("usuarios.json")

def load_users_from_file():
    """
    Carga el diccionario de usuarios desde usuarios.json.
    Si no existe o está vacío/dañado, regresa un dict vacío.
    """
    if not USERS_FILE.exists():
        return {}
    with open(USERS_FILE, "r", encoding="utf-8") as f:
        data = f.read().strip()
        if not data:
            return {}
        try:
            return json.loads(data)
        except Exception:
            # Si el contenido no es JSON válido, lo tratamos como vacío
            return {}

def save_users_to_file(users: dict) -> None:
    """
    Guarda el diccionario de usuarios en usuarios.json.
    """
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f, indent=2, ensure_ascii=False)

def verify_user_login(username: str, password: str, users: dict | None = None) -> bool:
    """
    Verifica usuario+password usando el esquema salt/hash
    guardado en el JSON.
    """
    if users is None:
        users = load_users_from_file()

    user = users.get(username)
    if not user:
        return False

    salt_b64 = user.get("salt")
    hash_b64 = user.get("hash")
    if not salt_b64 or not hash_b64:
        return False

    return verify_password(password, salt_b64, hash_b64)


# Usuarios demo en memoria (puedes persistirlos luego)
USERS = {
    "admin@lab.local": make_user("admin123", "admin"),
    "recep@lab.local": make_user("recep123", "recepcion"),
    "lab@lab.local":   make_user("lab123",   "lab"),
    # "med@lab.local" removed per request
}

# -------------------------
# CSV (cifrado en columnas sensibles)
# -------------------------
COLUMNS = [
    "Folio", "Fecha_Registro", "Fecha_Programada", "Costo_MXN",
    # Datos del paciente
    "Nombre_enc", "Edad", "Genero", "Telefono_enc", "Direccion_enc", "Emails_enc",
    # Orden / resultados
    "Tipo_Estudio", "Observaciones_enc", "Resultados_enc",
    "Estado"  # pendiente|capturado|firmado
]

def init_csv():
    if not os.path.exists(CSV_PATH):
        df = pd.DataFrame(columns=COLUMNS)
        df.to_csv(CSV_PATH, index=False)

def read_csv():
    init_csv()
    return pd.read_csv(CSV_PATH)

def write_csv(df: pd.DataFrame):
    df.to_csv(CSV_PATH, index=False)

def folio_auto():
    # Folio simple basado en tiempo (aaaaMMddHHmmss)
    return datetime.now().strftime("%Y%m%d%H%M%S")

def normalizar_telefono_mx(tel: str, default_country="+52"):
    if not tel: return ""
    digits = re.sub(r"\D", "", tel)
    if len(digits) == 10:
        return default_country + digits
    if tel.strip().startswith("+"):
        return tel.strip()
    return tel.strip()

def decrypt_view(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty: return df
    out = df.copy()
    out["Nombre"]    = out["Nombre_enc"].apply(dec)
    out["Telefono"]  = out["Telefono_enc"].apply(dec)
    out["Direccion"] = out["Direccion_enc"].apply(dec)
    out["Emails"]    = out["Emails_enc"].apply(dec) if "Emails_enc" in out.columns else ""
    out["Observaciones"] = out["Observaciones_enc"].apply(dec)
    out["Resultados"]    = out["Resultados_enc"].apply(dec)
    cols = [
        "Folio","Fecha_Registro","Fecha_Programada","Costo_MXN",
    "Nombre","Edad","Genero","Telefono","Direccion","Emails","Tipo_Estudio",
        "Observaciones","Resultados","Estado"
    ]
    return out.reindex(columns=cols)

def filter_df(df_dec: pd.DataFrame, query: str) -> pd.DataFrame:
    if not query: return df_dec
    q = query.lower()
    mask = pd.Series(False, index=df_dec.index)
    for col in df_dec.columns:
        mask |= df_dec[col].astype(str).str.lower().str.contains(q, na=False)
    return df_dec[mask]

def list_folios(status_filter=None):
    df = read_csv()
    if df.empty: return []
    if status_filter:
        df = df[df["Estado"].isin(status_filter)]
    return df["Folio"].astype(str).tolist()

def get_order_summary(folio: str):
    df = read_csv()
    if df.empty: return None
    row = df[df["Folio"].astype(str) == str(folio)]
    if row.empty: return None
    r = row.iloc[0].to_dict()
    return {
        "Folio": r["Folio"],
        "Fecha_Registro": r["Fecha_Registro"],
        "Fecha_Programada": r["Fecha_Programada"],
        "Estado": r["Estado"],
        "Tipo_Estudio": r.get("Tipo_Estudio", ""),
        "Nombre": dec(r.get("Nombre_enc","")),
        "Telefono": dec(r.get("Telefono_enc","")),
        "Direccion": dec(r.get("Direccion_enc","")),
        "Observaciones": dec(r.get("Observaciones_enc","")),
        "Resultados": dec(r.get("Resultados_enc","")),
    }

# -------------------------
# Catálogo de estudios desde Excel
# -------------------------
CATALOGO_XLSX = "catalogo_estudios.xlsx"
CATALOGO_SHEET = "Estudios"


def cargar_catalogo_estudios():
    """
    Lee el catálogo de estudios desde catalogo_estudios.xlsx.
    Filtra NaN y, si existe la columna 'Activo', deja solo los activos.
    """
    import os

    if not os.path.exists(CATALOGO_XLSX):
        # Regresamos un DF vacío pero con columnas esperadas
        return pd.DataFrame(
            columns=["Codigo", "Nombre", "Categoria", "Precio_MXN", "Activo"]
        )

    df = pd.read_excel(CATALOGO_XLSX, sheet_name=CATALOGO_SHEET)

    # Si tiene columna Activo, filtramos
    if "Activo" in df.columns:
        df = df[df["Activo"] == 1]

    # Quitamos filas sin nombre
    if "Nombre" in df.columns:
        df = df[df["Nombre"].notna()]

    return df


def lista_estudios(solo_activos: bool = True):
    """
    Devuelve la lista de nombres de estudios del catálogo.
    Si solo_activos=True y existe la columna 'Activo', filtra por activos.
    """
    df = cargar_catalogo_estudios()
    if df is None or df.empty:
        return []

    if solo_activos and "Activo" in df.columns:
        df = df[df["Activo"] == 1]

    if "Nombre" in df.columns:
        return df["Nombre"].astype(str).tolist()
    else:
        return []


def costo_total_desde_catalogo(nombres_estudios):
    """
    Calcula el costo total en MXN sumando los estudios que vengan en nombres_estudios.
    """
    df = cargar_catalogo_estudios()
    if df is None or df.empty:
        return 0.0

    sel = df[df["Nombre"].isin(nombres_estudios)]
    return float(sel["Precio_MXN"].fillna(0).sum())


# Catálogo de estudios (recortado/ajustable)
def save_order(
    folio, fecha_prog, costo, nombre, edad, genero, telefono, direccion,
    tipo, observaciones, emails=None
):
    df = read_csv()
    # normaliza tipo(s) a string unificado
    if isinstance(tipo, list):
        tipo_str = "; ".join([t for t in tipo if t])
    else:
        tipo_str = str(tipo or "").strip()

    # Procesar emails
    if emails:
        emails_str = "; ".join(emails)
    else:
        emails_str = ""

    row = {
        "Folio": folio or folio_auto(),
        "Fecha_Registro": datetime.now().isoformat(timespec="seconds"),
        "Fecha_Programada": fecha_prog if isinstance(fecha_prog, str) else str(fecha_prog),
        "Costo_MXN": float(costo) if costo else 0.0,
        "Nombre_enc": enc((nombre or "").strip()),
        "Edad": int(edad) if (edad is not None and str(edad).isdigit()) else None,
        "Genero": genero,
        "Telefono_enc": enc(normalizar_telefono_mx(telefono)),
        "Direccion_enc": enc((direccion or "").strip()),
        "Emails_enc": enc(emails_str),
        "Tipo_Estudio": tipo_str,
    "Observaciones_enc": enc((observaciones or "").strip()),
        "Resultados_enc": enc(""),
        "Estado": "pendiente"
    }
    df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    write_csv(df)
    return row["Folio"]

def save_results(folio, resultados_text, liberar=False):
    df = read_csv()
    if df.empty:
        raise ValueError("No hay base de datos.")
    m = df["Folio"].astype(str) == str(folio)
    if not m.any():
        raise ValueError(f"Folio no encontrado: {folio}")
    estado = "capturado"
    if liberar:
        estado = "firmado"
    df.loc[m, "Resultados_enc"] = enc(str(resultados_text or ""))
    df.loc[m, "Estado"] = estado
    write_csv(df)
    return True

def export_excel(df_dec: pd.DataFrame):
    df_dec.to_excel(XLSX_PATH, index=False)
    return XLSX_PATH, "Exportado a Excel."

LAB_INFO = _config["lab_info"]
DOCTOR_INFO = _config["doctor_info"]

def generar_pdf_resultado(
    solicitud,
    resultados,
    doctor_info=DOCTOR_INFO,
    lab_info=LAB_INFO,
    logo_path=LOGO_PATH,
    comentarios: str = "",
):
    """
    Genera un PDF limpio con:
    - Encabezado con logo + datos de LABZA
    - Datos del paciente / solicitud
    - Resultados de laboratorio
    - Comentarios + firma del médico
    """
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter

    # Helper para formatear fechas (acepta datetime o string tipo ISO)
    def _fmt_fecha(valor):
        from datetime import datetime, date

        if isinstance(valor, (datetime, date)):
            return valor.strftime("%Y-%m-%d")

        s = str(valor)
        if "T" in s:
            return s.split("T")[0] 

        return s   

    # ----- Encabezado con logo y nombre del laboratorio -----
    text_x = 50

    if logo_path and os.path.exists(str(logo_path)):
        try:
            logo = ImageReader(str(logo_path))
            logo_w = 60
            logo_h = 60
            c.drawImage(
                logo,
                50,
                height - 90,
                width=logo_w,
                height=logo_h,
                preserveAspectRatio=True,
                mask="auto",
            )
            text_x = 50 + logo_w + 15
        except Exception:
            text_x = 50

    c.setFont("Helvetica-Bold", 14)
    c.drawString(text_x, height - 40, lab_info.get("nombre", ""))

    c.setFont("Helvetica", 9)
    c.drawString(text_x, height - 55, lab_info.get("direccion", ""))
    c.drawString(
        text_x,
        height - 70,
        f"Tel: {lab_info.get('telefono', '')}  |  {lab_info.get('correo', '')}",
    )

    # línea divisoria
    c.line(50, height - 95, width - 50, height - 95)

    # ------------------------------------------------------------------
    # BLOQUE SUPERIOR: PACIENTE (IZQ) Y ESTUDIOS REALIZADOS (DER)
    # ------------------------------------------------------------------
    y_top = height - 120

    # ---- Datos del paciente a la izquierda ----
    y_left = y_top
    c.setFont("Helvetica-Bold", 10)
    c.drawString(50, y_left, "DATOS DEL PACIENTE")
    y_left -= 18
    c.setFont("Helvetica", 9)

    c.drawString(50, y_left, f"Nombre: {solicitud.get('nombre_paciente', '')}")
    y_left -= 14

    c.drawString(50, y_left, f"Folio: {solicitud.get('id_solicitud', '')}")
    y_left -= 14

    fecha_reg = _fmt_fecha(solicitud.get("fecha_registro", ""))
    fecha_muestra = _fmt_fecha(solicitud.get("fecha_muestra", ""))

    c.drawString(50, y_left, f"Fecha de registro: {fecha_reg}")
    y_left -= 14

    c.drawString(50, y_left, f"Fecha de toma de muestra: {fecha_muestra}")
    y_left -= 10

    y = y_left



    # ---- Estudios realizados a la derecha ----
    y_right = y_top
    x_right = width / 2 + 20

    if resultados:
        c.setFont("Helvetica-Bold", 10)
        c.drawString(x_right, y_right, "ESTUDIOS REALIZADOS")
        y_right -= 18
        c.setFont("Helvetica", 9)

        for nombre_estudio in resultados.keys():
            if y_right < 120:  # si se acaba espacio en la parte alta, pasamos de página
                c.showPage()
                width, height = letter
                y_top = height - 120
                y_left = y_top
                y_right = y_top
                c.setFont("Helvetica-Bold", 10)
                c.drawString(50, y_left, "DATOS DEL PACIENTE (cont.)")
                y_left -= 18
                c.setFont("Helvetica", 9)

            c.drawString(x_right + 10, y_right, f"- {nombre_estudio}")
            y_right -= 14

    # Punto de partida para la siguiente sección (tomamos el menor de ambos)
    y = min(y_left, y_right) - 24

     # ------------------------------------------------------------------
    # RESULTADOS
    # ------------------------------------------------------------------
    c.setFont("Helvetica-Bold", 10)
    c.drawString(50, y, "RESULTADOS DE LABORATORIO")
    y -= 22
    c.setFont("Helvetica", 9)

    if not resultados:
        c.drawString(50, y, "Sin resultados capturados.")
        y -= 18
    else:
        for nombre_estudio, info_est in resultados.items():
            if y < 160:  # dejamos margen para comentarios y pie de página
                c.showPage()
                width, height = letter
                y = height - 120
                c.setFont("Helvetica-Bold", 10)
                c.drawString(50, y, "RESULTADOS DE LABORATORIO (cont.)")
                y -= 22
                c.setFont("Helvetica", 9)

            valor = info_est.get("valor", "")
            unidad = info_est.get("unidad", "")
            ref = info_est.get("ref", "")
            linea = f"{nombre_estudio}: {valor} {unidad}"
            if ref:
                linea += f"   (Valores de referencia: {ref})"

            # ---- Word-wrap manual para líneas largas ----
            max_width = width - 100  # margen derecho
            words = linea.split(" ")
            current_line = ""

            for w in words:
                test_line = current_line + " " + w if current_line else w
                text_width = c.stringWidth(test_line, "Helvetica", 9)

                if text_width <= max_width:
                    current_line = test_line
                else:
                    # Pintar línea actual
                    c.drawString(50, y, current_line)
                    y -= 16  # espacio entre líneas del mismo resultado
                    current_line = w

            # Pintar última línea
            if current_line:
                c.drawString(50, y, current_line)
                y -= 20  # espacio entre diferentes resultados

           

    # ------------------------------------------------------------------
    # COMENTARIOS ADICIONALES EN RECUADRO
    # ------------------------------------------------------------------
    # Siempre mostramos la sección, aunque no haya texto
    texto_com = (comentarios or "").strip()
    if not texto_com:
        texto_com = "Sin comentarios adicionales."

    # espacio entre resultados y el título de comentarios
    y -= 80

    # si no cabe un recuadro decente, pasamos a nueva página
    if y < 200:
        c.showPage()
        width, height = letter
        y = height - 120

    c.setFont("Helvetica-Bold", 10)
    c.drawString(50, y, "COMENTARIOS ADICIONALES")
    y -= 18

    c.setFont("Helvetica", 9)
    line_height = 14
    padding = 6
    max_width = width - 100  # margen izq/der 50

    lineas = texto_com.splitlines()
    box_height = line_height * len(lineas) + padding * 2

    x_box = 50
    y_box_top = y
    y_box_bottom = y_box_top - box_height

    # dibujar rectángulo
    c.rect(x_box, y_box_bottom, max_width, box_height)

    # texto dentro del rectángulo
    y_text = y_box_top - padding - line_height
    for linea in lineas:
        if y_text < y_box_bottom + padding:
            break
        c.drawString(x_box + padding, y_text, linea)
        y_text -= line_height

    y = y_box_bottom - 20


    # ------------------------------------------------------------------
    # PIE DE PÁGINA: MÉDICO + FIRMA (SIEMPRE ABAJO)
    # ------------------------------------------------------------------
    footer_base = 60  # distancia desde la parte inferior

    # si el contenido se “comió” el footer, pasamos a nueva página
    if y < footer_base + 60:
        c.showPage()
        width, height = letter

    c.setFont("Helvetica", 9)
    # info del médico a la izquierda
    c.drawString(50, footer_base + 40, f"Médico responsable: {doctor_info.get('nombre', '')}")
    c.drawString(50, footer_base + 25, f"Cédula profesional: {doctor_info.get('cedula', '')}")
    c.drawString(50, footer_base + 10, f"Especialidad: {doctor_info.get('especialidad', '')}")

    # línea de firma a la derecha
    firma_x1 = width / 2 + 20
    firma_x2 = width - 50
    linea_y = footer_base + 25

    c.line(firma_x1, linea_y, firma_x2, linea_y)
    c.drawString(firma_x1, footer_base + 10, "Firma del médico")

    # Cerrar y devolver bytes
    c.save()
    buffer.seek(0)
    return buffer.getvalue()
