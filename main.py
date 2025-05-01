import os
import time
import logging
from dotenv import load_dotenv
import openai
import streamlit as st
import tempfile # Para manejar archivos temporales

# --- Configuración Inicial ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
load_dotenv()

# Configuración de la página de Streamlit (Modo oscuro se sugiere vía config.toml)
st.set_page_config(page_title="Asistente Legal", page_icon="⚖️", layout="wide")

# --- Cliente OpenAI ---
try:
    # Asegúrate de que la librería esté actualizada: pip install --upgrade openai
    client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    # Habilitar logs de la librería OpenAI (opcional, para depuración)
    # openai.log = "debug"
except Exception as e:
    st.error(f"Error al inicializar el cliente de OpenAI: {e}")
    st.stop()

# --- Cargar ID del Asistente ---
ASSISTANT_ID = os.getenv("ASSISTANT_ID")
if not ASSISTANT_ID:
    st.error("Error: La variable de entorno ASSISTANT_ID no está configurada.")
    st.stop()
# Verificar que el asistente tenga file_search habilitado (opcional pero recomendado)
try:
    assistant_info = client.beta.assistants.retrieve(ASSISTANT_ID)
    if not any(tool.type == 'file_search' for tool in assistant_info.tools):
         st.warning(f"Advertencia: El asistente '{assistant_info.name}' podría no tener la herramienta 'file_search' habilitada en la plataforma OpenAI. La subida de archivos podría no funcionar como se espera.")
except Exception as e:
    st.warning(f"No se pudo verificar la configuración de herramientas del asistente: {e}")


# --- Estado de la Sesión de Streamlit ---
# Inicializar variables de estado si no existen
if "thread_id" not in st.session_state:
    st.session_state.thread_id = None
if "messages" not in st.session_state:
    st.session_state.messages = [] # Lista para guardar el historial del chat
if "file_info_list" not in st.session_state:
    # Guardará diccionarios: {'file_id': id, 'filename': nombre}
    st.session_state.file_info_list = []
if "processing" not in st.session_state:
    st.session_state.processing = False # Para evitar doble envío

# --- Funciones Auxiliares ---

def upload_to_openai(filepath):
    """Sube un archivo a OpenAI y devuelve su ID."""
    try:
        with open(filepath, "rb") as file:
            # Usar 'assistants' como propósito es correcto
            response = client.files.create(file=file, purpose="assistants")
        logging.info(f"Archivo {os.path.basename(filepath)} subido a OpenAI con ID: {response.id}")
        return response.id
    except openai.APIError as e:
        logging.error(f"Error de API al subir archivo a OpenAI: {e}")
        st.error(f"Error de API al subir archivo {os.path.basename(filepath)}: {e.status_code} - {e.message}")
    except Exception as e:
        logging.error(f"Error inesperado al subir archivo a OpenAI: {e}")
        st.error(f"Error inesperado al subir archivo {os.path.basename(filepath)}.")
    return None

# Eliminar associate_file_with_assistant
# Eliminar remove_file_from_assistant

def delete_file_from_openai(file_id):
    """Elimina un archivo del almacenamiento de OpenAI."""
    try:
        logging.info(f"Intentando eliminar archivo de OpenAI: {file_id}")
        deleted_file = client.files.delete(file_id)
        logging.info(f"Respuesta de eliminación para {file_id}: {deleted_file}")
        return deleted_file.deleted
    except openai.APIError as e:
        logging.error(f"Error de API al eliminar archivo {file_id} de OpenAI: {e}")
        st.error(f"Error de API al eliminar archivo {file_id}: {e.status_code} - {e.message}")
    except Exception as e:
        logging.error(f"Error inesperado al eliminar archivo {file_id} de OpenAI: {e}")
        st.error(f"Error inesperado al eliminar archivo {file_id}.")
    return False

def process_message_with_citations(message):
    """Extrae contenido y anotaciones, formatea citas como notas al pie."""
    # (Sin cambios en esta función - parece correcta)
    try:
        # Asegurarse de que message.content no está vacío y tiene elementos
        if not message.content or len(message.content) == 0 or not hasattr(message.content[0], 'text'):
             logging.warning(f"Mensaje del asistente con formato inesperado o vacío: {message}")
             return "(Mensaje vacío o con formato no soportado)"

        message_content = message.content[0].text
        annotations = message_content.annotations if hasattr(message_content, "annotations") else []
        citations = []
        processed_text = message_content.value

        # Iterar sobre las anotaciones y añadir notas al pie
        for index, annotation in enumerate(annotations):
            # Reemplazar el texto anotado con un marcador de nota al pie
            processed_text = processed_text.replace(annotation.text, f" [{index + 1}]")

            # Recopilar información de la cita
            if file_citation := getattr(annotation, "file_citation", None):
                cited_file_id = file_citation.file_id
                # Intentar obtener el nombre del archivo citado desde OpenAI
                try:
                    # Verificar si el archivo está en nuestra lista de sesión primero
                    filename = next((f['filename'] for f in st.session_state.file_info_list if f['file_id'] == cited_file_id), None)
                    if not filename:
                        # Si no está en la sesión (quizás de una sesión anterior o conocimiento general), intentar recuperarlo
                        logging.info(f"Recuperando info del archivo citado: {cited_file_id}")
                        cited_file = client.files.retrieve(cited_file_id)
                        filename = cited_file.filename
                        logging.info(f"Nombre recuperado: {filename}")
                    citations.append(f'[{index + 1}] "{file_citation.quote}" (de {filename})')
                except Exception as e:
                    logging.warning(f"No se pudo obtener el nombre del archivo para {cited_file_id}: {e}")
                    filename = f"Archivo ID: {cited_file_id}" # Fallback
                    citations.append(f'[{index + 1}] "{file_citation.quote}" (de {filename})')


            elif file_path := getattr(annotation, "file_path", None):
                 # Este tipo de anotación generalmente apunta a un archivo generado por Code Interpreter
                 cited_file_id = file_path.file_id
                 try:
                    logging.info(f"Recuperando info del archivo generado: {cited_file_id}")
                    cited_file = client.files.retrieve(cited_file_id)
                    filename = cited_file.filename
                    logging.info(f"Nombre recuperado: {filename}")
                    # Nota: OpenAI no proporciona un enlace de descarga directo aquí.
                    # La cita podría indicar que el archivo fue generado.
                    citations.append(f'[{index + 1}] Referencia a archivo generado: {filename}')
                 except Exception as e:
                    logging.warning(f"No se pudo obtener el nombre del archivo para {cited_file_id}: {e}")
                    filename = f"Archivo ID: {cited_file_id}" # Fallback
                    citations.append(f'[{index + 1}] Referencia a archivo generado: {filename}')
            else:
                 # Otro tipo de anotación, o anotación sin detalles claros
                 citations.append(f'[{index + 1}] {annotation.text}')


        # Añadir las notas al pie al final del contenido del mensaje
        if citations:
            full_response = processed_text + "\n\n**Referencias:**\n" + "\n".join(citations)
        else:
            full_response = processed_text

        return full_response
    except Exception as e:
        logging.error(f"Error procesando mensaje con citas: {e}", exc_info=True)
        # Devolver el contenido original si hay error en el procesamiento
        return message.content[0].text.value if message.content and hasattr(message.content[0], 'text') else "(Error al procesar respuesta)"


# --- Interfaz de Usuario (Streamlit) ---

st.title("⚖️ Asistente Legal Colombiano")
st.caption("Consulta sobre leyes, decretos y jurisprudencia con ayuda de IA.")

# --- Sidebar para Gestión de Archivos ---
with st.sidebar:
    st.header("Gestión de Archivos")
    uploaded_file = st.file_uploader(
        "Sube documentos para análisis (PDF, DOCX, etc.No acepta archivos escaneados)" , key="file_upload", type=None # Acepta cualquier tipo, OpenAI validará
    )

    # Botón para subir (ya no asocia)
    if st.button("Subir Archivo", key="upload_button"):
        if uploaded_file:
            # Verificar si el archivo ya fue subido (por nombre)
            if any(f['filename'] == uploaded_file.name for f in st.session_state.file_info_list):
                st.warning(f"El archivo '{uploaded_file.name}' ya parece estar en la lista.")
            else:
                with st.spinner(f"Subiendo '{uploaded_file.name}'..."):
                    # Guardar temporalmente para obtener ruta
                    with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(uploaded_file.name)[1]) as tmp_file:
                        tmp_file.write(uploaded_file.getvalue())
                        tmp_file_path = tmp_file.name

                    # Subir a OpenAI
                    file_id = upload_to_openai(tmp_file_path)

                    if file_id:
                        # Guardar información en el estado de sesión (solo ID y nombre)
                        st.session_state.file_info_list.append({
                            'file_id': file_id,
                            'filename': uploaded_file.name
                            # No guardamos 'assistant_file_id'
                        })
                        st.success(f"Archivo '{uploaded_file.name}' subido con ID: {file_id}.")
                        st.rerun() # Refrescar para mostrar en la lista
                    else:
                        st.error(f"No se pudo subir el archivo '{uploaded_file.name}'.")

                    # Eliminar archivo temporal
                    try:
                        os.remove(tmp_file_path)
                    except Exception as e:
                        logging.warning(f"No se pudo eliminar el archivo temporal {tmp_file_path}: {e}")
        else:
            st.warning("Por favor, selecciona un archivo para subir.")

    st.divider()

    # Mostrar archivos subidos y opción para eliminar
    if st.session_state.file_info_list:
        st.subheader("Archivos Disponibles:")
        # Crear una copia para iterar y modificar el estado de sesión de forma segura
        files_to_display = list(st.session_state.file_info_list)
        for i, file_info in enumerate(files_to_display):
            col1, col2 = st.columns([0.8, 0.2])
            with col1:
                st.write(f"📄 {file_info['filename']}")
                st.caption(f"ID: {file_info['file_id']}") # Mostrar ID opcionalmente
            with col2:
                # Usar un key único para cada botón de eliminar
                if st.button("🗑️", key=f"delete_{file_info['file_id']}", help=f"Eliminar {file_info['filename']} de OpenAI"):
                    with st.spinner(f"Eliminando '{file_info['filename']}' de OpenAI..."):
                        # Solo necesitamos eliminar de OpenAI
                        deleted_from_openai = delete_file_from_openai(file_info['file_id'])

                        # Actualizar estado de sesión si tuvo éxito
                        if deleted_from_openai:
                            # Eliminar de la lista en el estado de sesión
                            st.session_state.file_info_list = [
                                f for f in st.session_state.file_info_list if f['file_id'] != file_info['file_id']
                            ]
                            st.success(f"Archivo '{file_info['filename']}' eliminado de OpenAI.")
                            st.rerun() # Refrescar la interfaz para actualizar la lista
                        else:
                            # El error ya se mostró en delete_file_from_openai
                            st.error(f"No se pudo completar la eliminación de '{file_info['filename']}'.")
    else:
        st.info("Sube archivos para que el asistente los analice.")

# --- Área Principal del Chat ---

# Mostrar mensajes existentes
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"]) # Usar markdown para formato

# Input del usuario
if prompt := st.chat_input("Escribe tu consulta aquí...", disabled=st.session_state.processing):
    st.session_state.processing = True # Bloquear input mientras se procesa

    # Añadir mensaje del usuario al historial y mostrarlo
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.spinner("Pensando..."):
        try:
            # Crear hilo si no existe
            if st.session_state.thread_id is None:
                logging.info("Creando nuevo hilo...")
                thread = client.beta.threads.create()
                st.session_state.thread_id = thread.id
                logging.info(f"Nuevo Thread ID: {st.session_state.thread_id}")

            # Construir la lista de adjuntos para el mensaje
            message_attachments = []
            if st.session_state.file_info_list:
                 message_attachments = [
                     {"file_id": file_info['file_id'], "tools": [{"type": "file_search"}]}
                     for file_info in st.session_state.file_info_list
                 ]
                 logging.info(f"Adjuntando {len(message_attachments)} archivo(s) al mensaje.")


            # Añadir mensaje al hilo de OpenAI CON los adjuntos
            client.beta.threads.messages.create(
                thread_id=st.session_state.thread_id,
                role="user",
                content=prompt,
                attachments=message_attachments # Adjuntar archivos aquí
            )
            logging.info(f"Mensaje añadido al hilo {st.session_state.thread_id}")

            # Crear y ejecutar el Run
            run = client.beta.threads.runs.create(
                thread_id=st.session_state.thread_id,
                assistant_id=ASSISTANT_ID,
                # Instrucciones específicas para este run
                instructions="""Por favor responde las preguntas basándote en los archivos adjuntos y tu conocimiento general sobre leyes colombianas.
                Cuando cites leyes (ej. Ley 1437 de 2011, Artículo 5), sentencias de la Corte Constitucional (ej. Sentencia C-355/06),
                o artículos de la Constitución Política de Colombia de 1991 (ej. Constitución Política, Artículo 29),
                por favor indica la referencia específica claramente en el texto o como una nota al pie al final de tu respuesta.
                Si citas directamente de un archivo adjunto, usa las herramientas de citación para indicarlo referenciando el archivo correcto.
                Distingue la información adicional que no proviene de los archivos con **negrita** o _subrayado_."""
            )
            logging.info(f"Run creado con ID: {run.id} para el hilo {st.session_state.thread_id}")

            # Esperar a que el Run se complete
            start_wait_time = time.time()
            while run.status not in ["completed", "failed", "cancelled", "expired", "requires_action"]:
                if time.time() - start_wait_time > 120: # Timeout de 2 minutos
                    logging.error(f"Timeout esperando el Run {run.id}")
                    st.error("La solicitud tardó demasiado en completarse.")
                    # Intentar cancelar el run (opcional)
                    try:
                        client.beta.threads.runs.cancel(thread_id=st.session_state.thread_id, run_id=run.id)
                    except Exception as cancel_e:
                        logging.error(f"Error al intentar cancelar el run {run.id}: {cancel_e}")
                    run.status = "failed" # Marcar como fallido localmente
                    break

                time.sleep(2) # Espera un poco más larga entre chequeos
                run = client.beta.threads.runs.retrieve(thread_id=st.session_state.thread_id, run_id=run.id)
                logging.info(f"Estado del Run {run.id}: {run.status}")

            if run.status == "completed":
                logging.info(f"Run {run.id} completado. Recuperando mensajes...")
                # Recuperar mensajes añadidos por el asistente en este Run
                # Es más fiable obtener todos los mensajes después del último del usuario y filtrar
                messages = client.beta.threads.messages.list(
                    thread_id=st.session_state.thread_id,
                    order="asc", # Pedir en orden ascendente para facilitar encontrar los nuevos
                    # Podríamos intentar filtrar por 'after' usando el ID del mensaje del usuario si lo guardáramos
                )
                logging.info(f"Recuperados {len(messages.data)} mensajes del hilo.")

                # Procesar y mostrar mensajes del asistente para este run específico
                assistant_messages_for_run = [
                    msg for msg in messages.data
                    if msg.run_id == run.id and msg.role == "assistant"
                ]
                logging.info(f"Encontrados {len(assistant_messages_for_run)} mensajes del asistente para el run {run.id}.")


                if assistant_messages_for_run:
                    for msg in assistant_messages_for_run:
                        full_response = process_message_with_citations(message=msg)
                        st.session_state.messages.append({"role": "assistant", "content": full_response})
                        # Mostrar inmediatamente el mensaje procesado
                        with st.chat_message("assistant"):
                            st.markdown(full_response, unsafe_allow_html=True)
                else:
                    # A veces, el mensaje puede tardar un instante más en aparecer después de que el run se completa
                    time.sleep(1)
                    messages = client.beta.threads.messages.list(thread_id=st.session_state.thread_id, order="desc", limit=5)
                    assistant_messages_for_run = [msg for msg in messages.data if msg.role == "assistant" and msg.run_id == run.id]
                    if assistant_messages_for_run:
                         logging.info("Mensaje del asistente encontrado en segundo intento.")
                         msg = assistant_messages_for_run[0] # Tomar el más reciente
                         full_response = process_message_with_citations(message=msg)
                         st.session_state.messages.append({"role": "assistant", "content": full_response})
                         with st.chat_message("assistant"):
                            st.markdown(full_response, unsafe_allow_html=True)
                    else:
                        logging.warning(f"No se encontraron mensajes del asistente para el run {run.id} incluso después de esperar.")
                        st.warning("El asistente no produjo una respuesta visible para esta consulta.")


            elif run.status == "requires_action":
                 logging.warning(f"Run {run.id} requiere acción (ej. tool call) - no implementado.")
                 st.warning("El asistente requiere una acción adicional que no está implementada.")
                 # Aquí iría la lógica para manejar 'tool_calls' si tu asistente los usa.

            else: # failed, cancelled, expired
                logging.error(f"Run {run.id} finalizó con estado: {run.status}")
                error_message = f"La consulta falló (Estado: {run.status})."
                last_error = getattr(run, 'last_error', None)
                if last_error:
                    error_message += f" Detalles: {last_error.message} (Código: {last_error.code})"
                st.error(error_message)
                # Añadir mensaje de error al historial para contexto
                st.session_state.messages.append({"role": "assistant", "content": f"Error: No se pudo completar la solicitud ({run.status})."})


        except openai.APIError as e:
            logging.error(f"Error de API de OpenAI: {e}", exc_info=True)
            st.error(f"Error de API: {e.status_code} - {e.message}")
            st.session_state.messages.append({"role": "assistant", "content": f"Error de API al procesar la solicitud."})
        except Exception as e:
            logging.error(f"Ocurrió un error inesperado: {e}", exc_info=True)
            st.error(f"Ocurrió un error inesperado: {e}")
            st.session_state.messages.append({"role": "assistant", "content": f"Error inesperado al procesar la solicitud."})
        finally:
            st.session_state.processing = False # Reactivar input
            st.rerun() # Forzar actualización de la interfaz para mostrar el último mensaje añadido

# Mensaje inicial si no hay chat
if not st.session_state.messages:
    st.info("Sube archivos en la barra lateral y haz tu primera consulta para comenzar.")
