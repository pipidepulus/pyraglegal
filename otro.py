import os
from dotenv import load_dotenv
import openai
import time
import logging

# --- Configuration ---
# Configurar el registro de errores
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Cargar variables de entorno
load_dotenv()

# Obtener la clave API y el ID del asistente desde las variables de entorno
API_KEY = os.getenv("OPENAI_API_KEY")
ASSISTANT_ID = os.getenv("ASSISTANT_ID") # <-- Cargar desde .env

if not API_KEY:
    logging.error("La variable de entorno OPENAI_API_KEY no está configurada.")
    exit(1)
if not ASSISTANT_ID:
    logging.error("La variable de entorno ASSISTANT_ID no está configurada.")
    exit(1)

# --- File Configuration ---
# Nombre del archivo local que quieres analizar
# Asegúrate de que este archivo exista en el mismo directorio que el script,
# o proporciona la ruta completa.
LOCAL_FILE_NAME = "PAL 19-21 Fiscal General.pdf"
# Construir la ruta completa al archivo (asumiendo que está en el mismo directorio)
LOCAL_FILE_PATH = os.path.join(os.path.dirname(__file__), LOCAL_FILE_NAME)


# Inicializar cliente de OpenAI
# Se recomienda inicializar el cliente una vez
try:
    client = openai.OpenAI(api_key=API_KEY)
except Exception as e:
    logging.error(f"Error al inicializar el cliente de OpenAI: {e}")
    exit(1)

# --- Assistant Interaction ---

# Mensaje para el asistente (ahora se refiere al archivo adjunto)
# Puedes ajustar este mensaje como prefieras.
message_content = f"Por favor, realiza un análisis detallado del documento adjunto ({LOCAL_FILE_NAME})."

# Función para esperar la finalización del run (sin cambios)
def wait_for_run_completion(client, thread_id, run_id, timeout_seconds=300, sleep_interval=5):
    """
    Espera a que un run se complete, falle, sea cancelado o expire.
    :param client: El cliente de OpenAI inicializado.
    :param thread_id: El ID del thread.
    :param run_id: El ID del run.
    :param timeout_seconds: Tiempo máximo de espera en segundos.
    :param sleep_interval: Tiempo en segundos entre verificaciones.
    :return: El objeto run finalizado o None si hay timeout o error.
    """
    start_time = time.time()
    while time.time() - start_time < timeout_seconds:
        try:
            run = client.beta.threads.runs.retrieve(thread_id=thread_id, run_id=run_id)
            status = run.status
            logging.info(f"Estado actual del Run: {status}")

            if status == "completed":
                logging.info("El proceso ha finalizado correctamente.")
                return run
            elif status in ["failed", "cancelled", "expired"]:
                logging.error(f"El Run ha terminado con estado: {status}")
                return run # Devolver el run aunque haya fallado para posible inspección
            elif status in ["queued", "in_progress", "requires_action"]:
                # Sigue esperando
                pass
            else:
                logging.warning(f"Estado del Run desconocido: {status}")
                # Considerar si seguir esperando o tratar como error

        except openai.APIError as e:
            logging.error(f"Error de API al verificar el estado del Run: {e}")
            return None # Salir en caso de error de API
        except Exception as e:
            logging.error(f"Error inesperado al verificar el estado del Run: {e}")
            return None # Salir en caso de error desconocido

        time.sleep(sleep_interval)

    logging.error(f"Timeout esperando la finalización del Run ID: {run_id}")
    return None # Indicar timeout

# --- Main Execution ---
uploaded_file_id = None
try:
    # 0. Subir el archivo local a OpenAI
    logging.info(f"Intentando subir el archivo: {LOCAL_FILE_PATH}")
    try:
        with open(LOCAL_FILE_PATH, "rb") as file_data:
            uploaded_file = client.files.create(file=file_data, purpose='assistants')
            uploaded_file_id = uploaded_file.id
            logging.info(f"Archivo '{LOCAL_FILE_NAME}' subido con éxito. File ID: {uploaded_file_id}")
    except FileNotFoundError:
        logging.error(f"Error: El archivo '{LOCAL_FILE_PATH}' no fue encontrado.")
        exit(1)
    except Exception as e:
        logging.error(f"Error al subir el archivo: {e}")
        exit(1)

    # 1. Recuperar información del asistente (Opcional, bueno para verificar)
    assistant_info = client.beta.assistants.retrieve(ASSISTANT_ID)
    logging.info(f"Usando asistente: {assistant_info.name} (ID: {assistant_info.id})")
    # Verificar si el asistente tiene la herramienta 'file_search' (o 'retrieval')
    # Esto es una verificación simple, la configuración real está en OpenAI
    has_file_search = any(tool.type == 'file_search' for tool in assistant_info.tools)
    if not has_file_search:
         # Si usa 'retrieval' (más antiguo), la lógica de adjuntar es diferente
         # pero 'file_search' es lo recomendado ahora.
         logging.warning(f"El asistente '{assistant_info.name}' podría no tener la herramienta 'file_search' habilitada. Asegúrate de que esté configurada en la plataforma de OpenAI.")


    # 2. Crear un hilo
    thread = client.beta.threads.create()
    thread_id = thread.id
    logging.info(f"Thread ID generado: {thread_id}")

    # 3. Añadir mensaje al hilo CON el archivo adjunto
    # Usamos el parámetro 'attachments' para asociar el archivo subido
    # y especificar que se use con la herramienta 'file_search'.
    message = client.beta.threads.messages.create(
        thread_id=thread_id,
        role="user",
        content=message_content,
        attachments=[
            {
                "file_id": uploaded_file_id,
                "tools": [{"type": "file_search"}] # O "code_interpreter" si el asistente lo usa para análisis
            }
        ]
    )
    logging.info("Mensaje con archivo adjunto añadido al hilo correctamente.")

    # 4. Ejecutar el asistente en el hilo
    run = client.beta.threads.runs.create(
        thread_id=thread_id,
        assistant_id=ASSISTANT_ID,
        # Las instrucciones específicas del run sobreescriben las del asistente
        # Puedes ajustar esto si es necesario
        instructions="Please address the user as Pipidepulus. Analiza el documento adjunto proporcionado usando la herramienta file_search."
    )
    run_id = run.id
    logging.info(f"Run iniciado con ID: {run_id}")

    # 5. Esperar a que el run se complete
    completed_run = wait_for_run_completion(client, thread_id, run_id)

    if completed_run and completed_run.status == 'completed':
        # 6. Recuperar los mensajes del hilo después de la finalización
        messages_response = client.beta.threads.messages.list(thread_id=thread_id, order="asc") # Pedir en orden ascendente
        messages_data = messages_response.data

        # Buscar la última respuesta del asistente (sin cambios)
        assistant_response = None
        for msg in reversed(messages_data): # Buscar desde el final
            if msg.role == "assistant":
                if msg.content and len(msg.content) > 0:
                    # Asumir que el contenido es de tipo texto
                    # Puede haber anotaciones de archivo aquí también
                    full_response_text = ""
                    for content_block in msg.content:
                        if content_block.type == 'text':
                            full_response_text += content_block.text.value
                            # Opcional: Mostrar anotaciones si existen
                            # annotations = content_block.text.annotations
                            # if annotations:
                            #     logging.info(f"Anotaciones encontradas: {annotations}")
                    assistant_response = full_response_text.strip()
                    break # Encontrar la primera respuesta completa del asistente desde el final
                break # Salir si se encuentra un mensaje de asistente (incluso si está vacío)


        if assistant_response:
            print("\n--- Respuesta del Asistente ---")
            print(assistant_response)
            print("-----------------------------\n")
        else:
            logging.warning("No se encontró una respuesta de texto del asistente en el hilo.")

        # 7. (Opcional) Verificar los pasos del run (sin cambios)
        try:
            run_steps_response = client.beta.threads.runs.steps.list(thread_id=thread_id, run_id=run_id)
            logging.info(f"Pasos del Run ({len(run_steps_response.data)}):")
            for step in run_steps_response.data:
                 logging.info(f"  - Step ID: {step.id}, Type: {step.type}, Status: {step.status}")
                 # Verificar detalles del paso, especialmente si usa 'file_search'
                 if step.step_details and step.step_details.type == 'tool_calls':
                     for tool_call in step.step_details.tool_calls:
                         if tool_call.type == 'file_search':
                             logging.info(f"    - Tool Call: file_search (Detalles: {tool_call.file_search})")

        except Exception as e:
            logging.error(f"Error al obtener los pasos del run: {e}")

    elif completed_run:
        logging.error(f"El Run no se completó correctamente. Estado final: {completed_run.status}")
        if completed_run.last_error:
             logging.error(f"Último error del Run: {completed_run.last_error.message}")
    else:
        # Timeout o error durante la espera
        logging.error("No se pudo obtener el estado final del Run.")


except openai.APIError as e:
    logging.error(f"Error de API de OpenAI: {e}")
except Exception as e:
    logging.error(f"Ocurrió un error inesperado en el flujo principal: {e}", exc_info=True) # Añadir traceback

# finally:
#     # 8. (Opcional pero recomendado) Eliminar el archivo subido de OpenAI para evitar acumulación
#     if uploaded_file_id:
#         try:
#             logging.info(f"Intentando eliminar el archivo subido: {uploaded_file_id}")
#             client.files.delete(uploaded_file_id)
#             logging.info(f"Archivo {uploaded_file_id} eliminado de OpenAI.")
#         except Exception as e:
#             logging.error(f"Error al eliminar el archivo {uploaded_file_id} de OpenAI: {e}")
