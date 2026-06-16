import os
import logging
from flask import Flask, request, jsonify, render_template
import openpyxl
from io import BytesIO
import requests

from db import load_env_from_dotenv, insert_fare_conversion, get_sysuuid

logging.basicConfig(level=logging.INFO)
load_env_from_dotenv()

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB max upload

# URL del endpoint del SP (Local para pruebas)
# SP_URL = "http://127.0.0.1:8000/snbrns-hub/hana/procedures/sp-snbrs-11"
SP_URL = "https://snbrns-processes-hub-noisy-baboon-ll.cfapps.us10.hana.ondemand.com/snbrns-hub/hana/procedures/sp-snbrs-11"

def call_sp(rows_read, rows_inserted_init, execution_id, user):
    """
    Llama al SP vía API enviando los datos reales requeridos.
    Retorna (success, message) leyendo el estatus interno de HANA.
    """
    try:
        logging.info("Llamando SP: %s", SP_URL)
        
        payload = {
            "rows_read": int(rows_read),
            "rows_inserted_init": int(rows_inserted_init),
            "execution_id_in": str(execution_id),
            "user": str(user)
        }
        
        response = requests.post(
            SP_URL,
            json=payload,
            timeout=300,
        )
        
        if response.status_code in [200, 201]:
            resp_json = response.json()
            
            # 1. Buscar la bandera de éxito en la raíz del JSON o dentro del arreglo 'rows'
            success_flag = resp_json.get("success_flag")
            if success_flag is None and "rows" in resp_json and len(resp_json["rows"]) > 0:
                success_flag = resp_json["rows"][0].get("SUCCESS_FLAG")
                
            # 2. Extraer el mensaje real devuelto por la Base de Datos
            db_message = resp_json.get("message")
            if not db_message and "rows" in resp_json and len(resp_json["rows"]) > 0:
                db_message = resp_json["rows"][0].get("MESSAGE")

            # 3. Validar si HANA reportó un error interno (success_flag == 0)
            if success_flag in [0, "0", False]:
                return False, f"El proceso falló en BD: {db_message}"
            else:
                return True, db_message or "Proceso finalizado con éxito."
        else:
            return (
                False,
                f"Error HTTP al llamar al SP: {response.status_code} — {response.text[:200]}",
            )
            
    except requests.exceptions.Timeout:
        return False, "Tiempo de espera agotado al llamar al SP (timeout 300s)."
    except ValueError:
        return False, "El SP no devolvió un JSON válido."
    except Exception as e:
        return False, f"Error conectando al SP: {str(e)}"

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/preview", methods=["POST"])
def preview():
    """
    Recibe el archivo Excel, lo parsea y devuelve las filas como JSON.
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

        header_index = next(
            (i for i, row in enumerate(all_rows) if row and row[0] == "TARIFAS PLANO"),
            None,
        )

        if header_index is None:
            return (
                jsonify({"error": "No se encontró la cabecera 'TARIFAS PLANO' en el archivo."}),
                422,
            )

        fare_rows = all_rows[header_index:]
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
    Recibe JSON con las filas a guardar y la cantidad leídas originalmente.
    Esperado en el body: 
    { 
        "rows": [[...], [...]], 
        "rows_read": 150  <-- Opcional: si tu Front lo manda. Si no, usaremos el len() de rows.
    }
    """
    body = request.get_json(silent=True)
    if not body or "rows" not in body:
        return jsonify({"error": "Cuerpo JSON inválido o sin campo 'rows'."}), 400

    raw_rows = body["rows"]
    if not raw_rows:
        return jsonify({"error": "No hay filas para guardar."}), 400

    # Si tu frontend envía "rows_read" lo toma de ahí, si no, asume el total de filas que llegaron por JSON
    rows_read = body.get("rows_read", len(raw_rows))

    records = [
        {
            "farePlain": row[0] if len(row) > 0 else None,
            "fareReport": row[1] if len(row) > 1 else None,
            "fareActual": row[2] if len(row) > 2 else None,
        }
        for row in raw_rows
    ]

    try:
        # 1. Insertar en la tabla temporal. Devuelve filas insertadas con éxito.
        rows_inserted_init = insert_fare_conversion(records, created_by="FARE_USER")
    except Exception as e:
        logging.exception("Error al insertar en HANA")
        return jsonify({"error": str(e)}), 500

    try:
        # 2. Generar el Execution ID usando tu función de BD
        execution_id = get_sysuuid()
    except Exception as e:
        logging.error("Error al generar el UUID: %s", str(e))
        execution_id = "FALLBACK_UUID" # Evita que rompa si get_sysuuid() falla

    # Usuario genérico solicitado en duro
    user_generic = "FARE_USER"

    # 3. Llamar al SP pasando los datos dinámicos reales
    logging.info("Llamando al SP después de la inserción...")
    sp_ok, sp_msg = call_sp(
        rows_read=rows_read,
        rows_inserted_init=rows_inserted_init,
        execution_id=execution_id,
        user=user_generic
    )
    
    if not sp_ok:
        logging.warning("SP respondió con error: %s", sp_msg)
        return (
            # jsonify({"error": f"Datos insertados ({rows_inserted_init} registros) pero el SP falló: {sp_msg}"}),
            jsonify({"error": f"Datos insertados ({rows_inserted_init} registros) pero el proceso interno falló. Revise que el archivo contenga datos válidos y sin duplicados."}),
            500,
        )

    # Si llegó aquí, fue estatus 200/201 del SP, devolvemos el mensaje simplificado de éxito.
    return jsonify(
        # {"message": f"{sp_msg} Se insertaron {rows_inserted_init} registros. La página se recargará automáticamente."}
        {"message": f"Se insertaron {rows_inserted_init} registros. La página se recargará automáticamente."}
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)