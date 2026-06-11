CREATE OR REPLACE PROCEDURE "SP_SNBRS_11"
(
    OUT success_flag INTEGER,
    OUT message NVARCHAR(1000)
)
LANGUAGE SQLSCRIPT
SQL SECURITY INVOKER 
AS
BEGIN

/*
  Nombre: SP_SNBRS_11
  Propósito: Carga de datos de conversión de tarifas desde tablas temporales a tablas RAW
  Fecha de creación: 2026-02-25
  Versión: 1.1
  Parámetros:
    - OUT success_flag: Indicador de éxito (1 = OK, 0 = error)
    - OUT message: Mensaje de error en caso de excepción
*/

    DECLARE v_step NVARCHAR(200);

    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        ROLLBACK;
        success_flag := 0;
        message := v_step || '. Detalle: ' || ::SQL_ERROR_MESSAGE;
    END;

    success_flag := 1;
    message := '';


    /**********************************************/
    /* TEMPFARECONVERSION: TEMP -> RAW               */
    /**********************************************/
    v_step := 'Error al insertar datos en GLOBALHITSS_EE_RAWFARECONVERSION_DEV';

    UPSERT "4A87446945C9455A8EAAFEC276742578"."GLOBALHITSS_EE_RAWFARECONVERSION_DEV"
    (
        "CREATEDAT",
        "CREATEDBY",
        "MODIFIEDAT",
        "MODIFIEDBY",
        "FAREPLAIN",
        "FAREREPORT",
        "FAREACTUAL"
    )
    SELECT
        "CREATEDAT",
        "CREATEDBY",
        "MODIFIEDAT",
        "MODIFIEDBY",
        "FAREPLAIN",
        "FAREREPORT",
        "FAREACTUAL"
    FROM "4A87446945C9455A8EAAFEC276742578"."GLOBALHITSS_EE_TEMPFARECONVERSION_DEV";


    /**********************************************/
    /* LIMPIEZA DE TABLAS TEMPORALES             */
    /**********************************************/

    v_step := 'Error al limpiar GLOBALHITSS_EE_TEMPFARECONVERSION_DEV';
    DELETE FROM "4A87446945C9455A8EAAFEC276742578"."GLOBALHITSS_EE_TEMPFARECONVERSION_DEV";


    COMMIT;

    message := 'Proceso conversión tarifas finalizado con éxito.';

END;