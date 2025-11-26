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



# -------------------------
# Config / archivos
# -------------------------
CSV_PATH   = "solicitudes_lis.csv"
XLSX_PATH  = "solicitudes_lis.xlsx"
KEY_PATH   = "fernet.key"   # demo; en producción usar bóveda/entorno

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


# -------------------------
# Catálogo de estudios desde Excel
# -------------------------
CATALOGO_XLSX = "catalogo_estudios.xlsx"
CATALOGO_SHEET = "Estudios"

def cargar_catalogo_estudios():
    import pandas as pd, os
    if not os.path.exists(CATALOGO_XLSX):
        return pd.DataFrame(columns=["Codigo","Nombre","Categoria","Precio_MXN","Activo"])
    df = pd.read_excel(CATALOGO_XLSX, sheet_name=CATALOGO_SHEET)
    expected = ["Codigo","Nombre","Categoria","Precio_MXN","Activo"]
    for col in expected:
        if col not in df.columns:
            df[col] = None
    if "Activo" in df.columns:
        df = df[df["Activo"] != False]
    df = df[df["Nombre"].notna()]
    return df

def lista_estudios():
    df = cargar_catalogo_estudios()
    return df["Nombre"].astype(str).tolist()

def costo_total_desde_catalogo(nombres_estudios):
    df = cargar_catalogo_estudios()
    if df.empty:
        return 0.0
    sel = df[df["Nombre"].isin(nombres_estudios)]
    return float(sel["Precio_MXN"].fillna(0).sum())

