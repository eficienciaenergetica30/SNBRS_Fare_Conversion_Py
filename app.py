import os
import logging
from flask import Flask, request, jsonify, render_template
import openpyxl
from io import BytesIO
import requests

from db import load_env_from_dotenv, insert_fare_conversion

logging.basicConfig(level=logging.INFO)
load_env_from_dotenv()

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB max upload

SP_URL = "https://snbrns-processes-hub-noisy-baboon-ll.cfapps.us10.hana.ondemand.com/snbrns-hub/hana/procedures/sp-snbrs-11"


def call_sp():
    """Llama al SP vía API y retorna (success, message)."""
    try:
        logging.info("Llamando SP: %s", SP_URL)
        response = requests.post(
            SP_URL,
            json={"param1": 0, "param2": "trigger_carga_horario_cfe"},
            timeout=300,
        )
        if response.status_code in [200, 201]:
            try:
                resp_json = response.json()
                if isinstance(resp_json, dict) and resp_json.get("success") is False:
                    return False, resp_json.get(
                        "message", "El proceso reportó un error sin detalle."
                    )
                return True, resp_json.get("message", "Proceso finalizado con éxito.")
            except ValueError:
                return True, "Proceso finalizado con éxito."
        else:
            return (
                False,
                f"Error HTTP al llamar al SP: {response.status_code} — {response.text[:200]}",
            )
    except requests.exceptions.Timeout:
        return False, "Tiempo de espera agotado al llamar al SP (timeout 300s)."
    except Exception as e:
        return False, f"Error conectando al SP: {str(e)}"


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/preview", methods=["POST"])
def preview():
    """
    Recibe el archivo Excel, lo parsea y devuelve las filas como JSON.
    Busca la fila con 'TARIFAS PLANO' como cabecera (igual que el JS original).
    """
    if "file" not in request.files:
        return jsonify({"error": "No se envió ningún archivo."}), 400

    file = request.files["file"]
    if not file.filename.endswith((".xlsx", ".xls")):
        return jsonify({"error": "El archivo debe ser .xlsx o .xls"}), 400

    try:
        wb = openpyxl.load_workbook(BytesIO(file.read()), data_only=True)
        ws = wb.active

        all_rows = [[cell.value for cell in row] for row in ws.iter_rows()]

        # Buscar la fila con 'TARIFAS PLANO' (igual que el JS)
        header_index = next(
            (i for i, row in enumerate(all_rows) if row and row[0] == "TARIFAS PLANO"),
            None,
        )

        if header_index is None:
            return (
                jsonify(
                    {
                        "error": "No se encontró la cabecera 'TARIFAS PLANO' en el archivo."
                    }
                ),
                422,
            )

        fare_rows = all_rows[header_index:]  # incluye cabecera
        header = fare_rows[0]
        data_rows = fare_rows[1:]

        # Filtrar filas completamente vacías
        data_rows = [r for r in data_rows if any(v is not None for v in r)]

        return jsonify({"header": header, "rows": data_rows})

    except Exception as e:
        logging.exception("Error al leer el archivo Excel")
        return jsonify({"error": str(e)}), 500


@app.route("/save", methods=["POST"])
def save():
    """
    Recibe JSON con la lista de filas y las inserta en HANA.
    Espera: { "rows": [[farePlain, fareReport, fareActual], ...] }
    """
    body = request.get_json(silent=True)
    if not body or "rows" not in body:
        return jsonify({"error": "Cuerpo JSON inválido o sin campo 'rows'."}), 400

    raw_rows = body["rows"]
    if not raw_rows:
        return jsonify({"error": "No hay filas para guardar."}), 400

    records = [
        {
            "farePlain": row[0] if len(row) > 0 else None,
            "fareReport": row[1] if len(row) > 1 else None,
            "fareActual": row[2] if len(row) > 2 else None,
        }
        for row in raw_rows
    ]

    try:
        count = insert_fare_conversion(records, created_by="WEB_USER")
    except Exception as e:
        logging.exception("Error al insertar en HANA")
        return jsonify({"error": str(e)}), 500

    # Solo llega aquí si el insert fue exitoso
    logging.info("Llamando al SP después de la inserción...")
    sp_ok, sp_msg = call_sp()
    if not sp_ok:
        logging.warning("SP respondió con error: %s", sp_msg)
        return (
            jsonify(
                {
                    "error": f"Datos insertados ({count} registros) pero el SP falló: {sp_msg}"
                }
            ),
            500,
        )

    return jsonify(
        {"message": f"Se insertaron {count} registros y el proceso finalizó correctamente. \n La pagina se recargará automáticamente."}
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
