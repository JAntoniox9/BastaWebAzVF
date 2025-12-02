from gevent import monkey
monkey.patch_all()
from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from flask_socketio import SocketIO, join_room, emit
import random, string, json, os, threading, time, hashlib, hmac, base64
from datetime import datetime, timedelta
from functools import wraps

from database import db, init_db, SalaDB

# Importar OpenAI para validación con IA
try:
    from openai import OpenAI
    from dotenv import load_dotenv
    load_dotenv()
    
    # Intentar configurar OpenAI
    try:
        openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        OPENAI_AVAILABLE = True
        print("✅ OpenAI configurado correctamente")
    except Exception:
        openai_client = None
        OPENAI_AVAILABLE = False
        print("⚠️ OpenAI no disponible")
    
except ImportError:
    OPENAI_AVAILABLE = False
    openai_client = None
    print("⚠️ Instala: pip install openai python-dotenv")
except Exception as e:
    OPENAI_AVAILABLE = False
    openai_client = None
    print(f"⚠️ Error configurando IA: {e}")


# ==========================================================
# CONFIGURACIÓN BASE
# ==========================================================
app = Flask(__name__)
app.secret_key = "basta_secret_2025"
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('AZURE_MYSQL_CONNECTIONSTRING')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
init_db(app)
socketio = SocketIO(app, cors_allowed_origins="*")
timers_activos = {}
iniciando_partida = set()

sid_to_room = {}
sid_to_name = {}
sid_to_player_id = {}  # Mapeo de socket ID a player ID
player_id_to_sid = {}  # Mapeo de player ID a socket IDs (un jugador puede tener múltiples conexiones)
player_id_counter = 0  # Contador para generar IDs únicos
admin_sockets = set()  # Sockets de administradores conectados

state = {"salas": {}}

# ==========================================================
# SISTEMA DE LOGS PARA ADMIN
# ==========================================================
def parse_user_agent(user_agent_string):
    """Parsea el User-Agent para obtener información del dispositivo y SO"""
    if not user_agent_string:
        return "Desconocido"
    
    ua = user_agent_string.lower()
    dispositivo = "Desktop"
    sistema_operativo = "Desconocido"
    navegador = "Desconocido"
    
    # Detectar dispositivo
    if "mobile" in ua or "android" in ua or "iphone" in ua or "ipad" in ua:
        if "tablet" in ua or "ipad" in ua:
            dispositivo = "Tablet"
        else:
            dispositivo = "Mobile"
    
    # Detectar sistema operativo
    if "windows" in ua:
        if "windows nt 10" in ua or "windows 10" in ua:
            sistema_operativo = "Windows 10/11"
        elif "windows nt 6.3" in ua:
            sistema_operativo = "Windows 8.1"
        elif "windows nt 6.2" in ua:
            sistema_operativo = "Windows 8"
        elif "windows nt 6.1" in ua:
            sistema_operativo = "Windows 7"
        else:
            sistema_operativo = "Windows"
    elif "mac os x" in ua or "macintosh" in ua:
        sistema_operativo = "macOS"
    elif "linux" in ua:
        sistema_operativo = "Linux"
    elif "android" in ua:
        sistema_operativo = "Android"
    elif "iphone" in ua or "ipad" in ua or "ios" in ua:
        sistema_operativo = "iOS"
    
    # Detectar navegador
    if "chrome" in ua and "edg" not in ua:
        navegador = "Chrome"
    elif "firefox" in ua:
        navegador = "Firefox"
    elif "safari" in ua and "chrome" not in ua:
        navegador = "Safari"
    elif "edg" in ua or "edge" in ua:
        navegador = "Edge"
    elif "opera" in ua or "opr" in ua:
        navegador = "Opera"
    
    return f"{dispositivo} | {sistema_operativo} | {navegador}"

def emit_admin_log(mensaje, tipo="info", sala="", ip=None, dispositivo_info=None):
    """Emite un log a la consola y al panel de admin"""
    timestamp = datetime.now().strftime("%H:%M:%S")
    
    # Construir mensaje con IP y dispositivo si están disponibles
    mensaje_completo = mensaje
    info_adicional = []
    
    if ip:
        info_adicional.append(f"IP: {ip}")
    
    if dispositivo_info:
        info_adicional.append(f"Dispositivo: {dispositivo_info}")
    
    if info_adicional:
        mensaje_completo = f"{mensaje} | {' | '.join(info_adicional)}"
    
    # Imprimir en consola
    print(f"[{timestamp}] {mensaje_completo}")
    
    # Emitir a los admins conectados
    if admin_sockets:
        socketio.emit('admin_log', {
            'timestamp': timestamp,
            'tipo': tipo,
            'mensaje': mensaje_completo,
            'sala': sala,
            'ip': ip or '',
            'dispositivo': dispositivo_info or ''
        }, room='admin_logs')

# ==========================================================
# CATEGORÍAS EXPANDIDAS CON ICONOS
# ==========================================================
CATEGORIAS_DISPONIBLES = {
    # Categorías Básicas
    "Nombre": {"icon": "👤", "dificultad": "facil"},
    "Animal": {"icon": "🦁", "dificultad": "facil"},
    "País o Ciudad": {"icon": "🌍", "dificultad": "facil"},
    "Fruta": {"icon": "🍎", "dificultad": "facil"},
    "Objeto": {"icon": "📦", "dificultad": "facil"},
    "Color": {"icon": "🎨", "dificultad": "facil"},
    
    # Categorías Intermedias
    "Profesión": {"icon": "👔", "dificultad": "normal"},
    "Canción": {"icon": "🎵", "dificultad": "normal"},
    "Artista musical": {"icon": "🎤", "dificultad": "normal"},
    "Videojuego": {"icon": "🎮", "dificultad": "normal"},
    "Marca": {"icon": "🏷️", "dificultad": "normal"},
    "Comida": {"icon": "🍕", "dificultad": "normal"},
    "Película": {"icon": "🎬", "dificultad": "normal"},
    "Serie de TV": {"icon": "📺", "dificultad": "normal"},
    
    # Categorías Difíciles
    "Monumento": {"icon": "🏛️", "dificultad": "dificil"},
    "Libro": {"icon": "📚", "dificultad": "dificil"},
    "Deporte": {"icon": "⚽", "dificultad": "dificil"},
    "Evento histórico": {"icon": "🎪", "dificultad": "dificil"},
    "Empresa": {"icon": "💼", "dificultad": "dificil"},
    "Personaje famoso": {"icon": "🌟", "dificultad": "dificil"},
    "Universidad": {"icon": "🎓", "dificultad": "dificil"},
    "Instrumento musical": {"icon": "🎸", "dificultad": "dificil"},
    "Superhéroe": {"icon": "🦸", "dificultad": "dificil"},
}

# ==========================================================
# CONFIGURACIÓN DE DIFICULTADES
# ==========================================================
DIFICULTADES = {
    "facil": {
        "nombre": "Fácil",
        "tiempo": 240,
        "num_categorias": 6,
        "puntos_unico": 100,
        "puntos_duplicado": 50
    },
    "normal": {
        "nombre": "Normal",
        "tiempo": 180,
        "num_categorias": 11,
        "puntos_unico": 100,
        "puntos_duplicado": 50
    },
    "dificil": {
        "nombre": "Difícil",
        "tiempo": 120,
        "num_categorias": 13,
        "puntos_unico": 150,
        "puntos_duplicado": 75
    },
    "extremo": {
        "nombre": "Extremo",
        "tiempo": 90,
        "num_categorias": 15,
        "puntos_unico": 200,
        "puntos_duplicado": 100
    }
}

# ==========================================================
# POWER-UPS DISPONIBLES
# ==========================================================
POWERUPS = {
    "tiempo_extra": {"nombre": "Tiempo Extra", "descripcion": "+30 segundos", "icon": "⏰", "costo": 1},
    "pista": {"nombre": "Pista", "descripcion": "Revela una letra", "icon": "💡", "costo": 2},
    "cambiar_letra": {"nombre": "Cambiar Letra", "descripcion": "Nueva letra aleatoria", "icon": "🔄", "costo": 3},
    "escudo": {"nombre": "Escudo", "descripcion": "Protege de duplicados", "icon": "🛡️", "costo": 2},
    "doble_puntos": {"nombre": "Doble Puntos", "descripcion": "X2 en próxima ronda", "icon": "💎", "costo": 3}
}

# ==========================================================
# FUNCIÓN AUXILIAR: SELECCIONAR CATEGORÍAS POR DIFICULTAD
# ==========================================================
def seleccionar_categorias_por_dificultad(dificultad):
    """
    Selecciona categorías aleatorias según la dificultad especificada.
    Retorna una lista de categorías seleccionadas.
    """
    config_dificultad = DIFICULTADES.get(dificultad, DIFICULTADES["normal"])
    num_cats = config_dificultad["num_categorias"]
    
    # Filtrar categorías según la dificultad seleccionada
    categorias_disponibles = []
    if dificultad == "facil":
        # Solo categorías fáciles
        categorias_disponibles = [cat for cat, info in CATEGORIAS_DISPONIBLES.items() 
                                 if info.get("dificultad") == "facil"]
    elif dificultad == "normal":
        # Categorías fáciles + normales
        categorias_disponibles = [cat for cat, info in CATEGORIAS_DISPONIBLES.items() 
                                 if info.get("dificultad") in ["facil", "normal"]]
    elif dificultad == "dificil":
        # Categorías fáciles + normales + difíciles
        categorias_disponibles = [cat for cat, info in CATEGORIAS_DISPONIBLES.items() 
                                 if info.get("dificultad") in ["facil", "normal", "dificil"]]
    else:  # extremo
        # Todas las categorías
        categorias_disponibles = list(CATEGORIAS_DISPONIBLES.keys())
    
    # Seleccionar aleatoriamente el número de categorías requeridas
    categorias = random.sample(categorias_disponibles, min(num_cats, len(categorias_disponibles)))
    return categorias

# ==========================================================
# MODOS DE JUEGO
# ==========================================================
MODOS_JUEGO = {
    "clasico": {"nombre": "Clásico", "descripcion": "Modo tradicional", "icon": "🎯"},
    "rapido": {"nombre": "Rápido", "descripcion": "5 categorías, 90 segundos", "icon": "⚡"},
    "equipos": {"nombre": "Equipos", "descripcion": "Juego en equipos", "icon": "🤝"},
    "duelo": {"nombre": "Duelo", "descripcion": "1 vs 1", "icon": "⚔️"},
    "eliminacion": {"nombre": "Eliminación", "descripcion": "El último es eliminado", "icon": "🔥"}
}


# ==========================================================
# FUNCIONES AUXILIARES
# ==========================================================
def load_state():
    """Carga el estado desde la base de datos MySQL"""
    try:
        salas_db = SalaDB.query.all()
        state = {"salas": {}}
        for sala_db in salas_db:
            if sala_db.datos:
                state["salas"][sala_db.codigo] = sala_db.datos
        return state
    except Exception as e:
        print(f"Error cargando estado desde BD: {e}")
        return {"salas": {}}

# Cargamos el estado al iniciar
state = load_state()

def save_state(state):
    """Guarda el estado en la base de datos MySQL usando upsert"""
    try:
        for codigo, datos_sala in state.get("salas", {}).items():
            sala_existente = SalaDB.query.get(codigo)
            if sala_existente:
                sala_existente.datos = datos_sala
            else:
                nueva_sala = SalaDB(codigo=codigo, datos=datos_sala)
                db.session.add(nueva_sala)
        db.session.commit()
    except Exception as e:
        print(f"Error guardando estado en BD: {e}")
        db.session.rollback()


def generar_codigo():
    letras = string.ascii_uppercase + string.digits
    return ''.join(random.choices(letras, k=5))

# ==========================================================
# SISTEMA DE FILTRADO DE CHAT
# ==========================================================
PALABRAS_PROHIBIDAS = {
    # Groserías comunes (versión censurable para académico)
    "puto", "puta", "pendejo", "pendeja", "idiota", "estupido", "estúpido",
    "mierda", "cabrón", "cabron", "hijo de puta", "chingar", "verga",
    "pinche", "mamon", "mamón", "culero", "joder", "coño",
    # Insultos
    "imbecil", "imbécil", "tonto", "tonta", "retrasado", "retrasada",
    "inutil", "inútil", "basura", "maldito", "maldita",
    # Variaciones
    "put0", "pend3jo", "m1erda", "c4bron"
}

def validar_nombre(nombre):
    """
    Valida que un nombre no contenga groserías o palabras vulgares
    Returns: (es_valido: bool, razon: str)
    """
    if not nombre or len(nombre.strip()) == 0:
        return False, "El nombre no puede estar vacío"
    
    nombre_lower = nombre.lower().strip()
    
    # Verificar longitud
    if len(nombre_lower) < 2:
        return False, "El nombre debe tener al menos 2 caracteres"
    
    if len(nombre) > 20:
        return False, "El nombre no puede tener más de 20 caracteres"
    
    # Verificar groserías
    for palabra_prohibida in PALABRAS_PROHIBIDAS:
        if palabra_prohibida in nombre_lower:
            return False, "El nombre contiene palabras inapropiadas"
    
    return True, "OK"

def moderar_mensaje_con_ia(mensaje):
    """
    Usa IA para detectar contenido inapropiado en mensajes de chat
    Returns: (es_apropiado, razon, mensaje_censurado)
    """
    if not OPENAI_AVAILABLE or not openai_client:
        return None, None, None  # Fallback a método tradicional
    
    try:
        prompt = f"""Analiza este mensaje de chat de un juego en línea y determina si es apropiado.

Mensaje: "{mensaje}"

Evalúa si contiene:
1. Groserías, insultos o lenguaje vulgar
2. Contenido ofensivo, discriminatorio o de odio
3. Acoso o bullying hacia otros jugadores
4. Contenido sexual o inapropiado
5. Spam o contenido sin sentido repetitivo

Responde EXACTAMENTE en este formato JSON:
{{"apropiado": true/false, "razon": "explicación breve si no es apropiado", "censurado": "mensaje con palabras inapropiadas reemplazadas por asteriscos si aplica"}}

Si el mensaje es apropiado, responde: {{"apropiado": true, "razon": "", "censurado": ""}}
Si no es apropiado, censura las palabras problemáticas con asteriscos del mismo largo.

IMPORTANTE: Solo marca como inapropiado si REALMENTE contiene contenido problemático. 
Mensajes normales de conversación, emojis, saludos, etc. son apropiados."""

        response = openai_client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "Eres un moderador de chat para un juego familiar. Debes ser estricto con groserías e insultos pero permisivo con conversación normal."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=200,
            temperature=0.1
        )
        
        respuesta = response.choices[0].message.content.strip()
        
        # Parsear JSON
        import json
        try:
            resultado = json.loads(respuesta)
            es_apropiado = resultado.get("apropiado", True)
            razon = resultado.get("razon", "")
            censurado = resultado.get("censurado", mensaje)
            
            return es_apropiado, razon, censurado if censurado else mensaje
        except json.JSONDecodeError:
            # Si no puede parsear, asumir apropiado
            return True, "", mensaje
            
    except Exception as e:
        print(f"⚠️ Error en moderación IA: {e}")
        return None, None, None  # Fallback a método tradicional

def filtrar_mensaje_chat(mensaje, sala=None, codigo_sala=""):
    """
    Filtra mensajes del chat usando IA y reglas de censura
    Returns: (mensaje_filtrado, es_valido, razon, tiene_groseria)
    """
    mensaje_original = mensaje
    mensaje_lower = mensaje.lower().strip()
    
    # 1. Verificar longitud
    if len(mensaje_lower) == 0:
        return "", False, "Mensaje vacío", False
    
    if len(mensaje) > 200:
        return "", False, "Mensaje muy largo (máx 200 caracteres)", False
    
    # 2. MODERAR CON IA (si está disponible)
    contiene_groseria = False
    mensaje_censurado = mensaje
    
    es_apropiado_ia, razon_ia, censurado_ia = moderar_mensaje_con_ia(mensaje)
    
    if es_apropiado_ia is not None:
        # IA disponible - usar su resultado
        if not es_apropiado_ia:
            contiene_groseria = True
            mensaje_censurado = censurado_ia if censurado_ia else mensaje
            emit_admin_log(f"🤖 [MODERACIÓN IA] Contenido inapropiado: {razon_ia}", "error", codigo_sala)
    else:
        # Fallback: usar lista de palabras prohibidas
        for palabra_prohibida in PALABRAS_PROHIBIDAS:
            if palabra_prohibida in mensaje_lower:
                contiene_groseria = True
                censura = "*" * len(palabra_prohibida)
                import re
                pattern = re.compile(re.escape(palabra_prohibida), re.IGNORECASE)
                mensaje_censurado = pattern.sub(censura, mensaje_censurado)
    
    # 3. VERIFICAR TRAMPA: letras prohibidas DENTRO de palabras
    if sala and sala.get("en_curso", False):
        letra_ronda = sala.get("letra", "").upper()
        if letra_ronda:
            import re
            palabras_encontradas = re.findall(r'[a-záéíóúñA-ZÁÉÍÓÚÑ]+', mensaje_lower)
            
            palabras_sospechosas = []
            for palabra in palabras_encontradas:
                if len(palabra) >= 3:
                    if palabra[0].upper() == letra_ronda:
                        palabras_sospechosas.append(palabra)
                        continue
                    
                    if len(palabra) >= 4:
                        for i in range(1, len(palabra) - 2):
                            if palabra[i].upper() == letra_ronda:
                                subcadena = palabra[i:]
                                if len(subcadena) >= 3:
                                    palabras_sospechosas.append(f"{palabra} (contiene '{subcadena}')")
                                    break
            
            if palabras_sospechosas:
                palabras_str = ", ".join(palabras_sospechosas[:3])
                return "", False, f"⚠️ Detectada posible trampa: palabras con '{letra_ronda}': {palabras_str}", False
    
    # 4. Filtrar spam (mismo mensaje repetido)
    if sala:
        mensajes_recientes = sala.get("mensajes_chat", [])[-5:]
        mensajes_recientes_texto = [m.get("mensaje", "") for m in mensajes_recientes if m.get("tipo") != "sistema"]
        
        if mensaje_lower in [m.lower() for m in mensajes_recientes_texto]:
            return "", False, "⚠️ No puedes enviar el mismo mensaje repetidamente", False
    
    # 5. Devolver mensaje censurado
    return mensaje_censurado, True, "OK", contiene_groseria

def crear_equipos_automaticamente(sala):
    """Crea equipos automáticamente dividiendo a los jugadores"""
    jugadores = sala.get("jugadores", [])
    num_jugadores = len(jugadores)
    
    if num_jugadores < 2:
        return
    
    # Mezclar jugadores
    jugadores_shuffled = jugadores.copy()
    random.shuffle(jugadores_shuffled)
    
    # Dividir en 2 equipos
    mitad = num_jugadores // 2
    equipo_a = jugadores_shuffled[:mitad]
    equipo_b = jugadores_shuffled[mitad:]
    
    sala["equipos"] = {
        "Equipo A": equipo_a,
        "Equipo B": equipo_b
    }
    
    sala["puntuaciones_equipos"] = {
        "Equipo A": 0,
        "Equipo B": 0
    }
    
    print(f"✅ Equipos creados: Equipo A: {equipo_a}, Equipo B: {equipo_b}")

# ==========================================================
# GENERAR PROMPT MEJORADO PARA VALIDACIÓN
# ==========================================================
def generar_prompt_validacion(respuesta, categoria, letra):
    """
    Genera un prompt mejorado para validación IA
    con reglas específicas según la categoría
    """
    # Obtener ejemplos específicos según la categoría
    ejemplos_categoria = ""
    reglas_especiales = ""
    ejemplos_incorrectos = ""
    
    categoria_lower = categoria.lower()
    
    # Formatear la pregunta de manera más directa según el tipo de categoría
    articulo = "un"
    if any(palabra in categoria_lower for palabra in ["serie", "película", "pelicula", "marca", "fruta", "verdura", "comida", "canción", "profesión", "universidad"]):
        articulo = "una"
    
    # Pregunta directa y simple - MUY DIRECTA
    pregunta_directa = f'¿"{respuesta}" es {articulo} {categoria}?'
    
    # Agregar ejemplos específicos de respuestas incorrectas según la categoría
    if "fruta" in categoria_lower:
        ejemplos_incorrectos = """
CASOS INCORRECTOS ESPECÍFICOS (responde NO):
- ¿"Rascacielos" es una Fruta? → NO - Rascacielos es un edificio/objeto, NO es una fruta
- ¿"Brasil" es una Fruta? → NO - Brasil es un país, NO es una fruta
- ¿"Perro" es una Fruta? → NO - Perro es un animal, NO es una fruta
- ¿"Reloj" es una Fruta? → NO - Reloj es un objeto, NO es una fruta
- ¿"Rugido" es una Fruta? → NO - Rugido es un sonido, NO es una fruta

CASOS CORRECTOS:
- ¿"Manzana" es una Fruta? → SI - Es una fruta válida
- ¿"Rosa" es una Fruta? → NO - Rosa es una flor, NO es una fruta (aunque algunas rosas producen frutos, "rosa" se refiere a la flor)
- ¿"Rambután" es una Fruta? → SI - Es una fruta válida"""
    elif "nombre" in categoria_lower:
        ejemplos_incorrectos = """
CASOS INCORRECTOS ESPECÍFICOS (responde NO):
- ¿"Radio" es un Nombre? → NO - Radio es un objeto/dispositivo, NO es un nombre de persona
- ¿"Río" es un Nombre? → NO - Río es un cuerpo de agua, NO es un nombre de persona
- ¿"Reloj" es un Nombre? → NO - Reloj es un objeto, NO es un nombre de persona
- ¿"Rugido" es un Nombre? → NO - Rugido es un sonido, NO es un nombre de persona
- ¿"Rascacielos" es un Nombre? → NO - Rascacielos es un edificio, NO es un nombre de persona

CASOS CORRECTOS:
- ¿"Roberto" es un Nombre? → SI - Es un nombre de persona válido
- ¿"Rosa" es un Nombre? → SI - Es un nombre de persona válido
- ¿"Ricardo" es un Nombre? → SI - Es un nombre de persona válido"""
    elif "color" in categoria_lower:
        ejemplos_incorrectos = """
CASOS INCORRECTOS ESPECÍFICOS (responde NO):
- ¿"Rugido" es un Color? → NO - Rugido es un sonido, NO es un color
- ¿"Río" es un Color? → NO - Río es un cuerpo de agua, NO es un color
- ¿"Reloj" es un Color? → NO - Reloj es un objeto, NO es un color
- ¿"Rascacielos" es un Color? → NO - Rascacielos es un edificio, NO es un color
- ¿"Rinoceronte" es un Color? → NO - Rinoceronte es un animal, NO es un color

CASOS CORRECTOS:
- ¿"Rojo" es un Color? → SI - Es un color válido
- ¿"Rosa" es un Color? → SI - Es un color válido
- ¿"Rubio" es un Color? → SI - Es un color válido (tinte de cabello)"""
    elif "país" in categoria_lower or "ciudad" in categoria_lower:
        ejemplos_incorrectos = """
CASOS INCORRECTOS ESPECÍFICOS (responde NO):
- ¿"Reloj" es un País? → NO - Reloj es un objeto, NO es un país o ciudad
- ¿"Río" es un País? → NO - Río es un cuerpo de agua, NO es un país (aunque existe "Río de Janeiro" como ciudad, "Río" solo no es válido)
- ¿"Rugido" es un País? → NO - Rugido es un sonido, NO es un país o ciudad
- ¿"Rascacielos" es un País? → NO - Rascacielos es un edificio, NO es un país o ciudad
- ¿"Rinoceronte" es un País? → NO - Rinoceronte es un animal, NO es un país o ciudad
- ¿"Manzana" es un País? → NO - Manzana es una fruta, NO es un país o ciudad

CASOS CORRECTOS:
- ¿"Brasil" es un País? → SI - Es un país válido
- ¿"Argentina" es un País? → SI - Es un país válido
- ¿"Roma" es una Ciudad? → SI - Es una ciudad válida
- ¿"Río de Janeiro" es una Ciudad? → SI - Es una ciudad válida"""
    elif "animal" in categoria_lower:
        ejemplos_incorrectos = """
CASOS INCORRECTOS ESPECÍFICOS (responde NO):
- ¿"Río" es un Animal? → NO - Río es un cuerpo de agua, NO es un animal
- ¿"Reloj" es un Animal? → NO - Reloj es un objeto, NO es un animal
- ¿"Rugido" es un Animal? → NO - Rugido es un sonido, NO es un animal
- ¿"Rascacielos" es un Animal? → NO - Rascacielos es un edificio, NO es un animal
- ¿"Manzana" es un Animal? → NO - Manzana es una fruta, NO es un animal

CASOS CORRECTOS:
- ¿"Rinoceronte" es un Animal? → SI - Es un animal válido
- ¿"Rata" es un Animal? → SI - Es un animal válido
- ¿"Rana" es un Animal? → SI - Es un animal válido"""
    elif "objeto" in categoria_lower:
        ejemplos_incorrectos = """
CASOS INCORRECTOS ESPECÍFICOS (responde NO):
- ¿"Rinoceronte" es un Objeto? → NO - Rinoceronte es un animal, NO es un objeto
- ¿"Río" es un Objeto? → NO - Río es un cuerpo de agua, NO es un objeto
- ¿"Rugido" es un Objeto? → NO - Rugido es un sonido, NO es un objeto
- ¿"Manzana" es un Objeto? → NO - Manzana es una fruta, NO es un objeto (aunque físicamente es un objeto, en el contexto del juego se refiere a cosas inanimadas fabricadas)

CASOS CORRECTOS:
- ¿"Reloj" es un Objeto? → SI - Es un objeto válido
- ¿"Radio" es un Objeto? → SI - Es un objeto válido
- ¿"Rascacielos" es un Objeto? → SI - Es un objeto/edificio válido"""
    elif "monumento" in categoria_lower:
        ejemplos_incorrectos = """
CASOS INCORRECTOS ESPECÍFICOS (responde NO):
- ¿"Brasil" es un Monumento? → NO - Brasil es un país, NO es un monumento
- ¿"Argentina" es un Monumento? → NO - Argentina es un país, NO es un monumento
- ¿"México" es un Monumento? → NO - México es un país, NO es un monumento
- ¿"Perro" es un Monumento? → NO - Perro es un animal, NO es un monumento
- ¿"Manzana" es un Monumento? → NO - Manzana es una fruta, NO es un monumento

CASOS CORRECTOS:
- ¿"Torre Eiffel" es un Monumento? → SI - Es un monumento famoso
- ¿"Estatua de la Libertad" es un Monumento? → SI - Es un monumento reconocido
- ¿"Coliseo" es un Monumento? → SI - Es un monumento histórico"""
    elif "alimento" in categoria_lower or "comida" in categoria_lower:
        ejemplos_incorrectos = """
CASOS INCORRECTOS ESPECÍFICOS (responde NO):
- ¿"Brasil" es un Alimento? → NO - Brasil es un país, NO es un alimento
- ¿"Argentina" es un Alimento? → NO - Argentina es un país, NO es un alimento
- ¿"Perro" es un Alimento? → NO - Perro es un animal, NO es un alimento (a menos que sea en contexto culinario específico)
- ¿"Torre Eiffel" es un Alimento? → NO - Torre Eiffel es un monumento, NO es un alimento

CASOS CORRECTOS:
- ¿"Manzana" es un Alimento? → SI - Es un alimento válido
- ¿"Pizza" es un Alimento? → SI - Es un alimento válido
- ¿"Arroz" es un Alimento? → SI - Es un alimento válido"""
    elif "país" in categoria_lower or "ciudad" in categoria_lower:
        ejemplos_incorrectos = """
CASOS INCORRECTOS ESPECÍFICOS (responde NO):
- ¿"Manzana" es un País? → NO - Manzana es una fruta, NO es un país
- ¿"Perro" es un País? → NO - Perro es un animal, NO es un país
- ¿"Torre Eiffel" es un País? → NO - Torre Eiffel es un monumento, NO es un país

CASOS CORRECTOS:
- ¿"Brasil" es un País? → SI - Es un país válido
- ¿"Argentina" es un País? → SI - Es un país válido"""
    elif "animal" in categoria_lower:
        ejemplos_incorrectos = """
CASOS INCORRECTOS ESPECÍFICOS (responde NO):
- ¿"Brasil" es un Animal? → NO - Brasil es un país, NO es un animal
- ¿"Manzana" es un Animal? → NO - Manzana es una fruta, NO es un animal
- ¿"Torre Eiffel" es un Animal? → NO - Torre Eiffel es un monumento, NO es un animal

CASOS CORRECTOS:
- ¿"Perro" es un Animal? → SI - Es un animal válido
- ¿"Gato" es un Animal? → SI - Es un animal válido"""
    elif "serie" in categoria_lower or "tv" in categoria_lower or "televisión" in categoria_lower:
        reglas_especiales = """
   - DEBE ser una serie de TV REAL y reconocible que exista o haya existido
   - NO aceptar nombres inventados (ej: "Zootopia Adventures" - no existe)
   - NO aceptar películas como series (ej: "Zootopia" es película, no serie)
   - NO aceptar títulos que suenan como series pero no existen
   - Verifica que sea una serie de TV real, no un título inventado"""
        ejemplos_categoria = """
- Pregunta: ¿"Breaking Bad" es una Serie de TV? → SI - Serie real y reconocible
- Pregunta: ¿"Zootopia Adventures" es una Serie de TV? → NO - No existe esta serie
- Pregunta: ¿"Game of Thrones" es una Serie de TV? → SI - Serie real y famosa
- Pregunta: ¿"Zootopia" es una Serie de TV? → NO - Es una película, no una serie"""
    elif "película" in categoria_lower or "pelicula" in categoria_lower:
        reglas_especiales = """
   - DEBE ser una película REAL que exista o haya existido
   - NO aceptar nombres inventados"""
        ejemplos_categoria = """
- Pregunta: ¿"Zootopia" es una Película? → SI - Película real de Disney
- Pregunta: ¿"Zootopia Adventures" es una Película? → NO - No existe esta película"""
    
    prompt = f"""Eres un validador experto de juego "BASTA/Stop".
Tu trabajo es validar si una respuesta corresponde CORRECTAMENTE a una categoría.

PREGUNTA PRINCIPAL (responde SI o NO):
{pregunta_directa}

⚠️ REGLAS CRÍTICAS ESTRICTAS (SIGUE ESTE ORDEN ESTRICTAMENTE):

1. ⚠️⚠️⚠️ VERIFICACIÓN DE CATEGORÍA (LO MÁS IMPORTANTE - VERIFICA ESTO PRIMERO) ⚠️⚠️⚠️:
   - ANTES de verificar la letra, pregunta: ¿"{respuesta}" es realmente {articulo} {categoria}?
   - Si "{respuesta}" es otra cosa (país, animal, fruta, monumento, objeto, color, nombre, parte del cuerpo, sonido, etc.) pero NO es {articulo} {categoria}, responde "NO" INMEDIATAMENTE
   - NO importa si empieza con la letra correcta, si NO es {articulo} {categoria}, la respuesta es "NO"

2. ⚠️ VERIFICACIÓN DE PALABRA VÁLIDA Y RECONOCIBLE:
   - "{respuesta}" DEBE ser una palabra REAL, RECONOCIBLE y que EXISTA en el idioma español
   - RECHAZA INMEDIATAMENTE si:
     * Parece una palabra inventada o mal escrita (ej: "Sasd", "asdas", "Sonso")
     * Es una variación mal escrita de otra palabra (ej: "NONDON" en lugar de "Londres")
     * Contiene repeticiones excesivas de letras (ej: "Negritoooo" con muchas 'o')
     * Es una combinación de palabras sin sentido (ej: "Nohay", "NOse", "Nomanches")
     * No es una palabra reconocible en español
     * Parece una combinación aleatoria de letras (ej: "asdas", "sasd")
     * Es un verbo cuando la categoría NO es "Verbo" o "Acción" (ej: "Salir" NO es un país)
   - Si no estás 100% seguro de que sea una palabra real y reconocible, responde "NO"
   - Si la palabra te parece extraña, inventada o no reconocible, responde "NO"

3. ⚠️ VERIFICACIÓN DE CORRESPONDENCIA ESPECÍFICA:
   - Para "Nombre": DEBE ser un nombre de persona real y reconocible (NO objetos, animales, lugares, etc.)
   - Para "Color": DEBE ser un color real y reconocible (NO sonidos, objetos, animales, etc.)
   - Para "Animal": DEBE ser un animal real y reconocible (NO objetos, partes del cuerpo, lugares, etc.)
   - Para "País o Ciudad": DEBE ser un país o ciudad real y reconocible (NO objetos, animales, variaciones mal escritas, etc.)
   - Para "Objeto": DEBE ser un objeto físico fabricado o creado (NO partes del cuerpo, animales, lugares, etc.)
   - Para "Fruta": DEBE ser una fruta real y reconocible (NO objetos, animales, lugares, expresiones, etc.)
   - Si "{respuesta}" NO corresponde específicamente a {categoria}, responde "NO"

4. VERIFICACIÓN DE LETRA (solo si pasó todas las verificaciones anteriores):
   - "{respuesta}" DEBE empezar con la letra "{letra}" (mayúscula o minúscula)
   - Si no empieza con "{letra}", responde NO

❌ REGLAS GENERALES DE RECHAZO (RECHAZA SI CUMPLE CUALQUIERA):
- Palabras inventadas, mal escritas o no reconocibles
- Variaciones mal escritas de palabras reales (ej: "NONDON" en lugar de "Londres")
- Combinaciones de palabras sin sentido (ej: "Nohay", "NOse", "Nomanches")
- Palabras con repeticiones excesivas de letras (ej: "Negritoooo")
- Respuestas que NO corresponden específicamente a la categoría {categoria}
- Partes del cuerpo cuando la categoría NO es "Parte del cuerpo" (ej: "Nariz" NO es un objeto)
- Sonidos cuando la categoría NO es "Sonido" (ej: "Rugido" NO es un color)
- Expresiones o frases cuando la categoría requiere una palabra específica
- Cualquier cosa que no sea claramente y específicamente {articulo} {categoria}

{ejemplos_incorrectos}

⚠️⚠️⚠️ INSTRUCCIÓN FINAL CRÍTICA ⚠️⚠️⚠️:
- PRIMERO: Verifica si "{respuesta}" es una palabra REAL y RECONOCIBLE
- SEGUNDO: Verifica si "{respuesta}" es realmente {articulo} {categoria} (NO otra cosa)
- TERCERO: Verifica que empiece con la letra "{letra}"
- Si NO cumple CUALQUIERA de estas condiciones, responde "NO" INMEDIATAMENTE
- Si no estás 100% seguro, responde "NO" (es mejor rechazar una respuesta dudosa que aceptar una incorrecta)
- La respuesta DEBE ser: palabra real + corresponder a {categoria} + empezar con "{letra}"

POLÍTICA DE VALIDACIÓN: SER ESTRICTO Y CONSERVADOR
- Rechaza cualquier respuesta que parezca dudosa, inventada, mal escrita o que no corresponda claramente a la categoría
- Es mejor rechazar 10 respuestas dudosas que aceptar 1 incorrecta
- Si hay CUALQUIER duda, responde "NO"

Responde SOLO "SI" o "NO" seguido de una razón breve.
Formato: "SI - razón" o "NO - razón"
"""
    return prompt




# ==========================================================
# VALIDACIÓN CON IA (OpenAI)
# ==========================================================
def validar_respuesta_con_ia(respuesta, categoria, letra):
    """
    Valida una respuesta usando IA de OpenAI
    Retorna: (es_valida: bool, razon: str, confianza: float)
    """
    
    # No validar respuestas vacías (ya se filtran antes)
    if not respuesta or len(respuesta.strip()) < 2:
        return False, "Respuesta demasiado corta", 1.0
    
    respuesta_limpia = respuesta.strip()
    respuesta_lower = respuesta_limpia.lower()
    
    # Detectar respuestas obviamente inválidas
    if len(set(respuesta_lower)) <= 2:  # Ej: "ññññññ", "aaaaa", "sis"
        return False, "Respuesta sin sentido (caracteres repetidos)", 1.0
    
    # Detectar palabras que parecen inventadas o sin sentido (patrones comunes)
    # Palabras muy cortas sin sentido (menos de 3 caracteres, excepto si son nombres comunes)
    if len(respuesta_limpia) < 3:
        if categoria.lower() not in ["nombre"]:  # Permitir nombres cortos como "Ana", "Luis"
            return False, "Respuesta demasiado corta o sin sentido", 1.0
    
    # Detectar combinaciones de letras que no forman palabras reconocibles
    # Patrones como "asdas", "sasd", "sonso", etc.
    if len(respuesta_limpia) >= 4:
        # Verificar si parece una palabra inventada (muchas consonantes seguidas o patrones extraños)
        vocales = set('aeiouáéíóúü')
        consonantes_seguidas = 0
        max_consonantes = 0
        for char in respuesta_lower:
            if char not in vocales and char.isalpha():
                consonantes_seguidas += 1
                max_consonantes = max(max_consonantes, consonantes_seguidas)
            else:
                consonantes_seguidas = 0
        
        # Si tiene 3 o más consonantes seguidas, probablemente es inventada
        if max_consonantes >= 3:
            return False, "Palabra no reconocible o inventada", 1.0
        
        # Detectar patrones comunes de palabras inventadas
        # Palabras que terminan en consonantes poco comunes o tienen patrones extraños
        patrones_inventados = ["asd", "sasd", "asdas", "qwerty", "zxcv", "hjkl", "fghj"]
        if any(patron in respuesta_lower for patron in patrones_inventados):
            return False, "Palabra no reconocible o inventada", 1.0
        
        # Detectar palabras que parecen combinaciones aleatorias (muchas consonantes alternadas)
        # Ej: "sasd", "asdas" tienen patrones CVCV o VCVCV que no son comunes en español
        if len(respuesta_limpia) == 4 or len(respuesta_limpia) == 5:
            # Contar vocales y consonantes
            num_vocales = sum(1 for c in respuesta_lower if c in vocales)
            num_consonantes = sum(1 for c in respuesta_lower if c.isalpha() and c not in vocales)
            
            # Si tiene muy pocas vocales para su longitud, probablemente es inventada
            if num_vocales == 0 and num_consonantes >= 3:
                return False, "Palabra no reconocible o inventada", 1.0
            
            # Si tiene un patrón muy regular CVCV o VCVCV y no es una palabra común, rechazar
            # (esto es una heurística, pero ayuda a detectar "sasd", "asdas")
            if num_vocales == num_consonantes and num_vocales <= 2:
                # Verificar si es una palabra común en español (lista básica)
                palabras_comunes_4_5 = {"casa", "mesa", "gato", "perro", "agua", "libro", "carta", "plato", "vaso", "silla", "mesa", "cama", "pelo", "mano", "pie", "ojo", "cara", "boca", "nariz", "diente", "brazo", "pierna", "hueso", "piel", "sangre", "hueso", "carne", "pan", "leche", "huevo", "queso", "azul", "rojo", "verde", "negro", "blanco", "gris", "amarillo", "rosa", "marrón", "naranja", "morado", "celeste", "verde", "azul"}
                if respuesta_lower not in palabras_comunes_4_5:
                    # Si no está en la lista y tiene un patrón sospechoso, rechazar
                    # (esto es conservador pero ayuda a detectar palabras inventadas)
                    pass  # No rechazar automáticamente, dejar que la IA decida
    
    # Detectar palabras que son verbos comunes cuando no corresponde
    verbos_comunes = {"salir", "entrar", "comer", "beber", "dormir", "hablar", "hacer", "decir", "ir", "venir", "ver", "saber", "poder", "querer", "tener", "estar", "ser"}
    if respuesta_lower in verbos_comunes:
        if categoria.lower() not in ["verbo", "acción"]:
            return False, f"'{respuesta_limpia}' es un verbo, no corresponde a la categoría", 1.0
    
    # USAR OPENAI (si está disponible)
    if OPENAI_AVAILABLE and openai_client:
        try:
            # Usar prompt mejorado (adaptado para JSON)
            prompt_base = generar_prompt_validacion(respuesta, categoria, letra)
            # Cambiar el formato de respuesta para JSON
            prompt = prompt_base.replace(
                'Responde SOLO "SI" o "NO" seguido de una razón breve.\nFormato: "SI - razón" o "NO - razón"',
                'Responde SOLO con formato JSON:\n{"valida": true/false, "razon": "explicación breve", "confianza": 0.0-1.0}'
            )

            response = openai_client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": "Eres un validador experto de juegos de palabras. Responde solo con JSON."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,
                max_tokens=150,
                timeout=5  # 5 segundos máximo
            )
            
            # Parsear respuesta
            resultado_texto = response.choices[0].message.content.strip()
            
            # Extraer JSON (puede venir con ```json o sin formato)
            if "```json" in resultado_texto:
                resultado_texto = resultado_texto.split("```json")[1].split("```")[0]
            elif "```" in resultado_texto:
                resultado_texto = resultado_texto.split("```")[1].split("```")[0]
            
            resultado = json.loads(resultado_texto.strip())
            
            es_valida = resultado.get("valida", False)
            razon = resultado.get("razon", "Sin razón especificada")
            confianza = resultado.get("confianza", 0.5)
            
            print(f"🤖 OpenAI validó '{respuesta}' ({categoria}): {'✓' if es_valida else '✗'} - {razon}")
            
            return es_valida, razon, confianza
            
        except json.JSONDecodeError as e:
            print(f"❌ Error parseando JSON de OpenAI: {e}")
            return True, "Error al procesar validación IA", 0.3
        except Exception as e:
            print(f"❌ Error en OpenAI: {e}")
            return True, "Error de validación IA", 0.3
    
    # Si OpenAI no está disponible, usar validación básica
    print(f"⚠️ OpenAI no disponible. Validación básica: '{respuesta}' ({'✓' if respuesta_limpia else '✗'})")
    # Validación básica: solo verificar que no esté vacía y empiece con la letra correcta
    return True, "Validación básica (IA no disponible)", 0.5


# ==========================================================
# FUNCIÓN DE PUNTUACIÓN
# ==========================================================
def calcular_puntuaciones(codigo):
    sala = state["salas"].get(codigo)
    if not sala:
        return None

    respuestas_por_jugador = sala.get("respuestas_ronda", {})
    letra = sala.get("letra", "?").upper()
    
    jugadores = list(sala.get("puntuaciones", {}).keys())
    if not jugadores:
         jugadores = sala.get("jugadores", [])
             
    puntuaciones_ronda = {jugador: 0 for jugador in jugadores}
    
    # 1. VALIDAR CON IA primero y agrupar respuestas válidas por categoría
    respuestas_validas_por_categoria = {}
    validaciones_ia = {}  # Almacenar resultados de IA para mostrar en UI
    
    print(f"🔍 Iniciando validación de {len(respuestas_por_jugador)} jugadores con letra '{letra}'")
    
    for jugador, respuestas in respuestas_por_jugador.items():
        if jugador not in jugadores: continue
        validaciones_ia[jugador] = {}
        
        for categoria, respuesta in respuestas.items():
            respuesta_limpia = respuesta.strip()
            
            if respuesta_limpia and len(respuesta_limpia) >= 2:
                # VALIDAR CON IA
                print(f"🤖 Validando: {jugador} - {categoria}: '{respuesta_limpia}'")
                es_valida_ia, razon_ia, confianza_ia = validar_respuesta_con_ia(
                    respuesta_limpia, categoria, letra
                )
                
                # Guardar resultado de validación IA
                validaciones_ia[jugador][categoria] = {
                    "validada_ia": es_valida_ia,
                    "razon_ia": razon_ia,
                    "confianza": confianza_ia,
                    "apelable": confianza_ia < 0.9  # Baja confianza = permitir apelación
                }
                
                print(f"   → Resultado: {'✓ Válida' if es_valida_ia else '✗ Inválida'} - {razon_ia}")
                
                # Solo agregar a válidas si IA aprueba Y empieza con letra correcta
                respuesta_upper = respuesta_limpia.strip().upper()
                if es_valida_ia and respuesta_upper.startswith(letra):
                    if categoria not in respuestas_validas_por_categoria:
                        respuestas_validas_por_categoria[categoria] = []
                    respuestas_validas_por_categoria[categoria].append(respuesta_upper)
            else:
                # Respuesta vacía o muy corta
                validaciones_ia[jugador][categoria] = {
                    "validada_ia": False,
                    "razon_ia": "Respuesta vacía o muy corta",
                    "confianza": 1.0,
                    "apelable": False
                }
                print(f"   → Respuesta vacía o muy corta")

    # 2. Calcular puntos para cada jugador
    modo_juego = sala.get("modo_juego", "clasico")
    multiplicador = 1.0
    
    # Aplicar multiplicadores según el modo
    if modo_juego == "rapido":
        multiplicador = 1.5  # Más puntos en modo rápido
    elif modo_juego == "duelo":
        multiplicador = 2.0  # Doble puntos en duelo
    
    for jugador, respuestas in respuestas_por_jugador.items():
        if jugador not in jugadores: continue
        for categoria, respuesta in respuestas.items():
            respuesta_limpia = respuesta.strip().upper()
            
            if respuesta_limpia and respuesta_limpia.startswith(letra):
                lista_respuestas = respuestas_validas_por_categoria.get(categoria, [])
                
                if lista_respuestas.count(respuesta_limpia) == 1:
                    puntuaciones_ronda[jugador] += int(100 * multiplicador)
                elif lista_respuestas.count(respuesta_limpia) > 1:
                    puntuaciones_ronda[jugador] += int(50 * multiplicador)

    # 3. Actualizar puntuaciones totales
    puntuaciones_totales = sala.get("puntuaciones", {j: 0 for j in jugadores})
    for jugador, puntos in puntuaciones_ronda.items():
        if jugador not in puntuaciones_totales:
            puntuaciones_totales[jugador] = 0
        puntuaciones_totales[jugador] += puntos
        
    sala["puntuaciones"] = puntuaciones_totales
    
    # 4. Si el modo es EQUIPOS, calcular puntuaciones de equipos
    modo_juego = sala.get("modo_juego", "clasico")
    puntuaciones_equipos = {}
    equipos = sala.get("equipos", {})
    
    if modo_juego == "equipos" and equipos:
        for nombre_equipo, miembros in equipos.items():
            puntos_equipo = sum(puntuaciones_totales.get(jugador, 0) for jugador in miembros)
            puntuaciones_equipos[nombre_equipo] = puntos_equipo
        
        sala["puntuaciones_equipos"] = puntuaciones_equipos
    
    # Calcular cuántos puntos dio cada respuesta por categoría
    puntos_por_respuesta = {}
    dificultad = sala.get("dificultad", "normal")
    config = DIFICULTADES.get(dificultad, DIFICULTADES["normal"])
    
    for jugador, respuestas in respuestas_por_jugador.items():
        if jugador not in jugadores: continue
        puntos_por_respuesta[jugador] = {}
        
        for categoria, respuesta in respuestas.items():
            respuesta_limpia = respuesta.strip().upper()
            puntos = 0
            
            if respuesta_limpia and respuesta_limpia.startswith(letra):
                # Verificar si la IA la validó
                validacion_jugador = validaciones_ia.get(jugador, {}).get(categoria, {})
                if validacion_jugador.get("validada_ia", False):
                    lista_respuestas = respuestas_validas_por_categoria.get(categoria, [])
                    
                    if lista_respuestas.count(respuesta_limpia) == 1:
                        puntos = int(config["puntos_unico"] * multiplicador)
                    elif lista_respuestas.count(respuesta_limpia) > 1:
                        puntos = int(config["puntos_duplicado"] * multiplicador)
            
            puntos_por_respuesta[jugador][categoria] = puntos
    
    # Guardar validaciones en la sala para que persistan (necesario para apelaciones)
    sala["validaciones_ia"] = validaciones_ia
    print(f"💾 Validaciones guardadas en sala. Total: {len(validaciones_ia)} jugadores")
    
    # Preparar categorías con iconos para el frontend
    categorias_sala = sala.get("categorias", [])
    categorias_con_info = []
    for cat in categorias_sala:
        info = CATEGORIAS_DISPONIBLES.get(cat, {})
        categorias_con_info.append({
            "nombre": cat,
            "icon": info.get("icon", "📝")
        })
    
    results_packet = {
        "ronda": sala.get("ronda_actual"),
        "letra": sala.get("letra", "?"),
        "categorias": categorias_con_info,
        "scores_ronda": puntuaciones_ronda,
        "scores_total": puntuaciones_totales,
        "respuestas": respuestas_por_jugador,
        "validaciones_ia": validaciones_ia,  # Nueva: resultados de validación IA
        "puntos_por_respuesta": puntos_por_respuesta,  # Nueva: puntos por cada respuesta
        "anfitrion": sala.get("anfitrion"),
        "modo_juego": modo_juego,
        "equipos": equipos,
        "puntuaciones_equipos": puntuaciones_equipos
    }
    
    print(f"📦 Results packet preparado con validaciones_ia: {len(validaciones_ia)} jugadores")
    return results_packet

# ==========================================================
# RUTAS PRINCIPALES
# ==========================================================
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/create")
def create_page():
    return render_template("crear_sala.html")

@app.route("/join")
def join_page():
    return render_template("unirse_sala.html")


@app.route("/create_room", methods=["POST"])
def create_room_route():
    try:
        if request.is_json:
            data = request.get_json()
        else:
            data = request.form.to_dict()
            
        nombre = data.get("nombre", "Anfitrión").strip()
        
        # Validar nombre (sin groserías)
        es_valido, razon = validar_nombre(nombre)
        if not es_valido:
            return jsonify({"ok": False, "error": razon}), 400
        rondas = int(data.get("rondas", 3))
        dificultad = data.get("dificultad", "normal")
        modo_juego = data.get("modo_juego", "clasico")
        categorias_personalizadas = data.get("categorias", None)
        # TODAS LAS FUNCIONALIDADES ACTIVADAS POR DEFECTO (admin puede desactivar)
        powerups_habilitados = data.get("powerups_habilitados", True)
        chat_habilitado = data.get("chat_habilitado", True)
        sonidos_habilitados = data.get("sonidos_habilitados", True)
        validacion_activa = data.get("validacion_activa", True)

        codigo = generar_codigo()
        
        # Seleccionar categorías según configuración
        if categorias_personalizadas and isinstance(categorias_personalizadas, list):
            categorias = categorias_personalizadas
        else:
            # Seleccionar categorías según dificultad usando la función auxiliar
            categorias = seleccionar_categorias_por_dificultad(dificultad)

        # Asignar ID al anfitrión
        global player_id_counter
        player_id_counter += 1
        anfitrion_id = f"P{player_id_counter:06d}"
        
        state["salas"][codigo] = {
            "anfitrion": nombre,
            "jugadores": [nombre],
            "rondas": rondas,
            "estado": "espera",
            "puntuaciones": {nombre: 0},
            "respuestas_ronda": {},
            "ronda_actual": 1,
            "jugadores_listos": [nombre],
            "jugadores_desconectados": [],  # Lista de jugadores que se desconectaron
            
            # Sistema de IDs de jugadores
            "jugadores_ids": {nombre: anfitrion_id},  # {nombre_jugador: player_id}
            "ids_jugadores": {anfitrion_id: nombre},  # {player_id: nombre_jugador}
            
            # Configuración avanzada
            "dificultad": dificultad,
            "modo_juego": modo_juego,
            "categorias": categorias,
            "categorias_personalizadas": categorias_personalizadas if (categorias_personalizadas and isinstance(categorias_personalizadas, list)) else None,
            "powerups_habilitados": powerups_habilitados,
            "chat_habilitado": chat_habilitado,
            "sonidos_habilitados": sonidos_habilitados,
            "validacion_activa": validacion_activa,
            
            # Sistema de equipos
            "equipos": {},  # {"Equipo A": [jugador1, jugador2], "Equipo B": [jugador3, jugador4]}
            "puntuaciones_equipos": {},  # {"Equipo A": 0, "Equipo B": 0}
            
            # Sistema de chat
            "mensajes_chat": [],
            
            # Power-ups de jugadores
            "powerups_jugadores": {nombre: {"tiempo_extra": 0, "pista": 0, "cambiar_letra": 0, "escudo": 0, "doble_puntos": 0}},
            
            # Sistema de validación
            "respuestas_cuestionadas": {},
            "votos_validacion": {},
            
            # Penalizaciones
            "penalizaciones": {nombre: 0},
            
            # Estado de partida
            "finalizada": False,  # Indica si la partida ya finalizó
            "pausada": False,  # Indica si la ronda está pausada
        }

        save_state(state)
        
        # Obtener IP y dispositivo para el log
        ip = get_client_ip()
        user_agent = request.headers.get('User-Agent', '')
        dispositivo_info = parse_user_agent(user_agent)
        
        emit_admin_log(f"✅ Sala creada | Anfitrión: {nombre} | Dificultad: {dificultad} | Modo: {modo_juego}", "success", codigo, ip=ip, dispositivo_info=dispositivo_info)
        return jsonify({"codigo": codigo, "ok": True})

    except Exception as e:
        print(f"❌ Error al crear sala: {e}")
        return jsonify({"ok": False, "error": str(e)}), 400


@app.route("/join_room", methods=["POST"])
def join_room_route():
    try:
        if request.is_json:
            data = request.get_json()
            nombre = data.get("nombre", "Jugador").strip()
            codigo = data.get("codigo", "").strip().upper()
        else:
            nombre = request.form.get("nombre", "Jugador").strip()
            codigo = request.form.get("codigo", "").strip().upper()

        # Validar nombre (sin groserías)
        es_valido, razon = validar_nombre(nombre)
        if not es_valido:
            return jsonify({"ok": False, "error": razon}), 400

        if codigo not in state["salas"]:
            return jsonify({"ok": False, "error": "La sala no existe."}), 404

        if nombre in state["salas"][codigo]["jugadores"]:
            return jsonify({"ok": True, "codigo": codigo})

        state["salas"][codigo]["jugadores"].append(nombre)
        state["salas"][codigo]["puntuaciones"][nombre] = 0
        
        # Obtener IP y dispositivo para el log
        ip = get_client_ip()
        user_agent = request.headers.get('User-Agent', '')
        dispositivo_info = parse_user_agent(user_agent)
        
        emit_admin_log(f"👥 Jugador {nombre} se unió a sala {codigo}", "join", codigo, ip=ip, dispositivo_info=dispositivo_info)

        return jsonify({"ok": True, "codigo": codigo})

    except Exception as e:
        print(f"❌ Error al unirse: {e}")
        return jsonify({"ok": False, "error": str(e)}), 400
    

@app.route("/waiting/<codigo>")
def waiting_room(codigo):
    sala = state["salas"].get(codigo)

    if not sala:
        return "❌ Sala no encontrada", 404

    # Verificar si la partida está finalizada
    if sala.get("finalizada", False):
        return render_template("partida_finalizada.html", codigo=codigo)

    jugadores = sala.get("jugadores", [])
    anfitrion = sala["anfitrion"]
    
    ronda_actual = sala.get("ronda_actual", 1)
    total_rondas = sala.get("rondas", 1)
    fin_del_juego = ronda_actual > total_rondas

    return render_template(
        "waiting.html",
        jugadores=jugadores,
        anfitrion=anfitrion,
        codigo=codigo,
        puntuaciones=sala.get("puntuaciones", {}),
        fin_del_juego=fin_del_juego,
        jugadores_listos=sala.get("jugadores_listos", []),
        jugadores_desconectados=sala.get("jugadores_desconectados", [])
    )


@app.route("/start/<codigo>")
def start_game(codigo):
    jugador = request.args.get("jugador")
    sala = state["salas"].get(codigo)

    if not sala:
        return "❌ Sala no encontrada", 404

    if sala.get("en_curso", False):
        return "⚠️ Ya hay una partida activa en esta sala. Espera a que termine."

    if len(sala["jugadores"]) < 2:
        return "⚠️ Debe haber al menos 2 jugadores para iniciar."

    anfitrion = sala.get("anfitrion")
    if jugador and jugador != anfitrion:
        return f"🚫 Solo el anfitrión ({anfitrion}) puede iniciar el juego.", 403

    letra = random.choice("ABCDEFGHIJKLMNÑOPQRSTUVWXYZ")
    sala["letra"] = letra
    
    ronda_actual = sala.get("ronda_actual", 1) 
    sala["ronda_actual"] = ronda_actual
    
    # NUEVO: Guardar timestamp de inicio de ronda para validar tiempo mínimo de BASTA
    sala["inicio_ronda_timestamp"] = time.time()
    
    # Limpiar respuestas de la ronda anterior y apelaciones
    sala["respuestas_ronda"] = {}
    sala["apelaciones"] = {}
    
    # Obtener tiempo según dificultad y modo
    dificultad = sala.get("dificultad", "normal")
    modo_juego = sala.get("modo_juego", "clasico")
    config_dificultad = DIFICULTADES.get(dificultad, DIFICULTADES["normal"])
    tiempo_ronda = config_dificultad["tiempo"]
    
    # Modificar tiempo según el modo
    if modo_juego == "rapido":
        tiempo_ronda = min(tiempo_ronda, 90)  # Máximo 90 segundos en modo rápido
    
    # NUEVO: Seleccionar nuevas categorías en cada ronda según la dificultad
    # Solo si no hay categorías personalizadas (que se mantienen fijas)
    categorias_personalizadas = sala.get("categorias_personalizadas", None)
    if not categorias_personalizadas or not isinstance(categorias_personalizadas, list):
        # Seleccionar nuevas categorías aleatorias para esta ronda
        nuevas_categorias = seleccionar_categorias_por_dificultad(dificultad)
        sala["categorias"] = nuevas_categorias
        print(f"🎲 Nuevas categorías seleccionadas para ronda {ronda_actual}: {nuevas_categorias}")
    
    # CREAR EQUIPOS AUTOMÁTICAMENTE si el modo es "equipos"
    if modo_juego == "equipos" and ronda_actual == 1:
        crear_equipos_automaticamente(sala)
        print(f"⚽ Modo EQUIPOS activado - Equipos creados para sala {codigo}")
    
    sala["basta"] = False
    sala["en_curso"] = True
    sala["pausada"] = False  # Inicializar estado de pausa
    sala["tiempo_restante"] = tiempo_ronda
    sala["respuestas_ronda"] = {}
    sala["jugadores_listos"] = []
    save_state(state)

    emit_admin_log(f"🎯 Letra generada: {letra} (Ronda {ronda_actual})", "game", codigo)

    # Emitir información de equipos si están activos
    emit_data = {"letra": letra, "codigo": codigo}
    if modo_juego == "equipos":
        emit_data["equipos"] = sala.get("equipos", {})
        emit_data["modo_equipos"] = True
    
    # Emitir evento para redirigir SOLO a los usuarios que están en game.html a waiting
    # Esto asegura que los invitados que están viendo resultados vuelvan a waiting
    # El anfitrión que está en waiting.html NO será redirigido (ya está ahí)
    socketio.emit("redirect_to_waiting", {"codigo": codigo}, room=codigo)
    
    # Pequeño delay para asegurar que los usuarios en game.html reciban la redirección
    time.sleep(0.3)
    
    # Ahora emitir start_game que redirigirá a TODOS (anfitrión e invitados) desde waiting a game
    socketio.emit("start_game", emit_data, room=codigo)
    threading.Thread(target=temporizador_ronda, args=(codigo,)).start()
    
    # Redirigir al anfitrión directamente a game.html
    # Los invitados serán redirigidos por el evento start_game desde waiting.html
    return redirect(url_for("game", codigo=codigo))


@app.route("/game/<codigo>")
def game(codigo):
    if codigo not in state["salas"]:
        return "Sala no encontrada", 404

    sala = state["salas"][codigo]
    
    # Verificar si la partida está finalizada
    if sala.get("finalizada", False):
        return render_template("partida_finalizada.html", codigo=codigo)
    
    letra = sala.get("letra", "?")

    # Obtener configuración del modo de juego
    modo_juego = sala.get("modo_juego", "clasico")
    
    # Obtener categorías según el modo
    categorias = sala.get("categorias", list(CATEGORIAS_DISPONIBLES.keys())[:11])
    
    # Aplicar modificaciones según el modo
    if modo_juego == "rapido":
        categorias = categorias[:5]  # Solo 5 categorías
    elif modo_juego == "duelo" and len(sala["jugadores"]) != 2:
        # Modo duelo requiere exactamente 2 jugadores
        return "⚠️ El modo Duelo requiere exactamente 2 jugadores.", 400
    
    # Preparar categorías con iconos
    categorias_con_iconos = []
    for cat in categorias:
        icon = CATEGORIAS_DISPONIBLES.get(cat, {}).get("icon", "📝")
        categorias_con_iconos.append({"nombre": cat, "icon": icon})

    return render_template("game.html",
                           jugador=sala["anfitrion"],
                           codigo=codigo,
                           ronda=sala.get("ronda_actual", 1),
                           total_rondas=sala.get("rondas", 1),
                           letra=letra,
                           categorias=categorias_con_iconos,
                           powerups_habilitados=sala.get("powerups_habilitados", True),
                           chat_habilitado=sala.get("chat_habilitado", True),
                           validacion_activa=sala.get("validacion_activa", False))



# ==========================================================
# FUNCIONES DE CONTROL DE TIEMPO
# ==========================================================
def temporizador_ronda(codigo):
    with app.app_context():
        sala = state["salas"].get(codigo, {})
        duracion = sala.get("tiempo_restante", 180)
        print(f"🕒 Temporizador iniciado para sala {codigo} con {duracion} segundos")
        timers_activos[codigo] = True

        s = duracion
        while s > 0:
            if not timers_activos.get(codigo, True):
                print(f"⏹️ Temporizador cancelado para sala {codigo}")
                return
            
            sala = state["salas"].get(codigo, {})
            
            # Verificar si está pausado
            if sala.get("pausada", False):
                # Emitir estado de pausa cada segundo mientras está pausado
                socketio.emit("update_timer", {"tiempo": s, "pausada": True}, room=codigo)
                time.sleep(0.5)
                continue
            
            sala["tiempo_restante"] = s
            socketio.emit("update_timer", {"tiempo": s, "pausada": sala.get("pausada", False)}, room=codigo)
            s -= 1
            time.sleep(1)

        sala = state["salas"].get(codigo, {})
        if sala.get("basta_activado", False):
            print(f"⚠️ Ronda {codigo} ya terminada por ¡BASTA!, no iniciar conteo doble")
            return

        sala["basta_activado"] = True
        save_state(state)
        print(f"⏰ Tiempo agotado en sala {codigo}")
        socketio.emit("basta_triggered", {"motivo": "Tiempo agotado"}, room=codigo)
        threading.Thread(target=conteo_final, args=(codigo,)).start()


# ==========================================================
# EVENTOS SOCKETIO
# ==========================================================
@socketio.on("connect")
def on_connect():
    print("✅ Nuevo cliente conectado.")

@socketio.on("admin_join_logs")
def on_admin_join_logs():
    """Admin se une al canal de logs"""
    admin_sockets.add(request.sid)
    join_room('admin_logs')
    emit_admin_log("🔐 Admin conectado al monitor de logs", "success")
    print(f"🔐 Admin conectado: {request.sid}")

@socketio.on("disconnect")
def on_disconnect():
    """Limpiar admin socket al desconectar"""
    if request.sid in admin_sockets:
        admin_sockets.discard(request.sid)
        print(f"🔐 Admin desconectado: {request.sid}")

@socketio.on("host_is_starting")
def handle_host_starting(data):
    jugador = data.get("jugador")
    if jugador:
        iniciando_partida.add(jugador)
        print(f"🚦 {jugador} está en transición para iniciar el juego...")


@socketio.on("player_ready")
def handle_player_ready(data):
    codigo = data.get("codigo")
    jugador = data.get("jugador")
    
    # Validar que el jugador tenga un nombre válido
    if not jugador or jugador == "null" or jugador == "undefined" or str(jugador).strip() == "" or str(jugador).lower() == "none":
        print(f"⚠️ Intento de marcar como listo con nombre inválido: {jugador}")
        return
    
    sala = state["salas"].get(codigo)
    
    if sala and jugador:
        if jugador not in sala.get("jugadores_listos", []):
            sala.setdefault("jugadores_listos", []).append(jugador)
            save_state(state)
            
            socketio.emit(
                "player_joined",
                {
                    "jugadores": sala["jugadores"],
                    "puntuaciones": sala.get("puntuaciones", {}),
                    "jugadores_listos": sala.get("jugadores_listos", []),
                    "jugadores_desconectados": sala.get("jugadores_desconectados", []),
                    "configuracion": {
                        "rondas": sala.get("rondas", 3),
                        "dificultad": sala.get("dificultad", "normal"),
                        "modo_juego": sala.get("modo_juego", "clasico"),
                        "chat_habilitado": sala.get("chat_habilitado", True),
                        "sonidos_habilitados": sala.get("sonidos_habilitados", True),
                        "powerups_habilitados": sala.get("powerups_habilitados", True),
                        "validacion_activa": sala.get("validacion_activa", True)
                    }
                },
                room=codigo
            )

@socketio.on("join_room_event")
def handle_join(data):
    codigo = data.get("codigo")
    jugador = data.get("jugador", "Invitado")
    
    # Validar que el jugador tenga un nombre válido
    if not jugador or jugador == "null" or jugador == "undefined" or str(jugador).strip() == "" or str(jugador).lower() == "none":
        print(f"⚠️ Intento de unirse con nombre inválido: {jugador}")
        return

    sid_to_room[request.sid] = codigo
    sid_to_name[request.sid] = jugador
    join_room(codigo)

    sala = state["salas"].get(codigo)

    if sala:
        # Inicializar sistema de IDs de jugadores si no existe
        if "jugadores_ids" not in sala:
            sala["jugadores_ids"] = {}  # {nombre_jugador: player_id}
        if "ids_jugadores" not in sala:
            sala["ids_jugadores"] = {}  # {player_id: nombre_jugador}
        
        if jugador not in sala["jugadores"]:
            sala["jugadores"].append(jugador)
            if jugador not in sala["puntuaciones"]:
                sala["puntuaciones"][jugador] = 0
            
            # Asignar ID único si no tiene uno
            if jugador not in sala["jugadores_ids"]:
                global player_id_counter
                player_id_counter += 1
                player_id = f"P{player_id_counter:06d}"
                sala["jugadores_ids"][jugador] = player_id
                sala["ids_jugadores"][player_id] = jugador
            save_state(state)
        
        # Asignar player_id al socket
        player_id = sala["jugadores_ids"].get(jugador)
        if player_id:
            sid_to_player_id[request.sid] = player_id
            if player_id not in player_id_to_sid:
                player_id_to_sid[player_id] = []
            if request.sid not in player_id_to_sid[player_id]:
                player_id_to_sid[player_id].append(request.sid)
            
        iniciando_partida.discard(jugador)

        socketio.emit(
            "player_joined",
            {
                "jugadores": sala["jugadores"],
                "puntuaciones": sala.get("puntuaciones", {}),
                "jugadores_listos": sala.get("jugadores_listos", []),
                "jugadores_desconectados": sala.get("jugadores_desconectados", []),
                "configuracion": {
                    "rondas": sala.get("rondas", 3),
                    "dificultad": sala.get("dificultad", "normal"),
                    "modo_juego": sala.get("modo_juego", "clasico"),
                    "chat_habilitado": sala.get("chat_habilitado", True),
                    "sonidos_habilitados": sala.get("sonidos_habilitados", True),
                    "powerups_habilitados": sala.get("powerups_habilitados", True),
                    "validacion_activa": sala.get("validacion_activa", True)
                }
            },
            room=codigo
        )

        socketio.emit(
            "restore_state",
            {
                "letra": sala.get("letra", "?"),
                "tiempo_restante": sala.get("tiempo_restante", 0),
                "ronda": sala.get("ronda_actual", 1)
            },
            room=request.sid
        )

        # Obtener IP y dispositivo para el log (desde SocketIO)
        ip = get_client_ip_from_environ()
        user_agent = get_user_agent_from_environ()
        dispositivo_info = parse_user_agent(user_agent)
        
        emit_admin_log(f"👥 {jugador} se unió a la sala {codigo}", "join", codigo, ip=ip, dispositivo_info=dispositivo_info)


@socketio.on("disconnect")
def on_disconnect():
    sid = request.sid
    codigo = sid_to_room.pop(sid, None)
    jugador = sid_to_name.pop(sid, None)
    if not codigo:
        return

    sala = state["salas"].get(codigo)
    if not sala:
        return
        
    if jugador in iniciando_partida:
        print(f"⚠️ Ignorando desconexión temporal de {jugador} (iniciando partida)")
        return
        
    def verificar_salida():
        with app.app_context():
            time.sleep(3)
            
            jugador_sigue_conectado = False
            for sid_activo, nombre_activo in sid_to_name.items():
                if nombre_activo == jugador and sid_to_room.get(sid_activo) == codigo:
                    jugador_sigue_conectado = True
                    break
            
            if jugador_sigue_conectado:
                return

            sala = state["salas"].get(codigo)
            if not sala:
                return

            anfitrion = sala.get("anfitrion")
            
            if jugador in sala.get("jugadores_listos", []):
                sala["jugadores_listos"].remove(jugador)

            if jugador == anfitrion:
                # Reasignar anfitrión en lugar de eliminar la sala
                sala["jugadores"].remove(jugador)
                
                if len(sala["jugadores"]) > 0:
                    # Hay otros jugadores: reasignar anfitrión
                    nuevo_anfitrion = sala["jugadores"][0]
                    sala["anfitrion"] = nuevo_anfitrion
                    
                    # Asegurarse que el nuevo anfitrión esté en la lista de listos
                    if nuevo_anfitrion not in sala["jugadores_listos"]:
                        sala["jugadores_listos"].append(nuevo_anfitrion)
                    
                    save_state(state)
                    
                    print(f"👑 {jugador} salió. Nuevo anfitrión: {nuevo_anfitrion} en sala {codigo}")
                    
                    # Notificar cambio de anfitrión
                    socketio.emit("nuevo_anfitrion", {
                        "nuevo_anfitrion": nuevo_anfitrion,
                        "mensaje": f"👑 {nuevo_anfitrion} es ahora el anfitrión"
                    }, room=codigo)
                    
                    # Actualizar lista de jugadores
                    socketio.emit("player_joined", {
                        "jugadores": sala["jugadores"],
                        "puntuaciones": sala.get("puntuaciones", {}),
                        "jugadores_listos": sala.get("jugadores_listos", []),
                        "jugadores_desconectados": sala.get("jugadores_desconectados", []),
                        "configuracion": {
                            "rondas": sala.get("rondas", 3),
                            "dificultad": sala.get("dificultad", "normal"),
                            "modo_juego": sala.get("modo_juego", "clasico"),
                            "chat_habilitado": sala.get("chat_habilitado", True),
                            "sonidos_habilitados": sala.get("sonidos_habilitados", True),
                            "powerups_habilitados": sala.get("powerups_habilitados", True),
                            "validacion_activa": sala.get("validacion_activa", True)
                        }
                    }, room=codigo)
                else:
                    # No hay más jugadores: eliminar sala
                    print(f"👋 ANFITRIÓN {jugador} salió y no hay más jugadores. Eliminando sala {codigo}.")
                    timers_activos[codigo] = False
                    if codigo in state["salas"]:
                        del state["salas"][codigo]
                    save_state(state)

            elif jugador in sala["jugadores"]:
                # Marcar como desconectado en lugar de eliminar
                if "jugadores_desconectados" not in sala:
                    sala["jugadores_desconectados"] = []
                if jugador not in sala["jugadores_desconectados"]:
                    sala["jugadores_desconectados"].append(jugador)
                save_state(state)
                socketio.emit("player_joined", {
                    "jugadores": sala["jugadores"],
                    "puntuaciones": sala.get("puntuaciones", {}),
                    "jugadores_listos": sala.get("jugadores_listos", []),
                    "jugadores_desconectados": sala.get("jugadores_desconectados", []),
                    "configuracion": {
                        "rondas": sala.get("rondas", 3),
                        "dificultad": sala.get("dificultad", "normal"),
                        "modo_juego": sala.get("modo_juego", "clasico"),
                        "chat_habilitado": sala.get("chat_habilitado", True),
                        "sonidos_habilitados": sala.get("sonidos_habilitados", True),
                        "powerups_habilitados": sala.get("powerups_habilitados", True),
                        "validacion_activa": sala.get("validacion_activa", True)
                    }
                }, room=codigo)
                print(f"👋 {jugador} salió de la sala {codigo}")

                if len(sala["jugadores"]) == 0:
                    print(f"🗑️ Eliminando sala {codigo} (sin jugadores)")
                    timers_activos[codigo] = False
                    if codigo in state["salas"]:
                        del state["salas"][codigo]
                    save_state(state)

    threading.Thread(target=verificar_salida).start()

@socketio.on("basta_pressed")
def handle_basta(data):
    codigo = data.get("codigo")
    sala = state["salas"].get(codigo)

    # Verificar si la partida está finalizada
    if sala and sala.get("finalizada", False):
        print(f"⚠️ ¡BASTA! ignorado: partida ya finalizada en sala {codigo}")
        socketio.emit("partida_finalizada", {"mensaje": "La partida ya ha finalizado"}, room=request.sid)
        return
    
    # Verificar si la ronda está pausada
    if sala and sala.get("pausada", False):
        socketio.emit("ronda_pausada", {
            "pausada": True,
            "mensaje": "La ronda está pausada. No puedes presionar ¡BASTA!"
        }, room=request.sid)
        return
    
    # NUEVO: Verificar tiempo mínimo antes de poder presionar BASTA (30 segundos)
    TIEMPO_MINIMO_BASTA = 30  # segundos
    inicio_ronda = sala.get("inicio_ronda_timestamp", 0)
    tiempo_transcurrido = time.time() - inicio_ronda
    
    if tiempo_transcurrido < TIEMPO_MINIMO_BASTA:
        segundos_restantes = int(TIEMPO_MINIMO_BASTA - tiempo_transcurrido)
        socketio.emit("basta_rechazado", {
            "mensaje": f"⏳ Debes esperar {segundos_restantes} segundos más antes de presionar ¡BASTA!",
            "segundos_restantes": segundos_restantes
        }, room=request.sid)
        emit_admin_log(f"⚠️ BASTA rechazado: muy pronto ({int(tiempo_transcurrido)}s)", "error", codigo)
        return

    if sala and not sala.get("basta_activado", False):
        sala["basta_activado"] = True
        save_state(state)
        timers_activos[codigo] = False
        emit_admin_log(f"✋ ¡BASTA! presionado", "game", codigo)
        socketio.emit("basta_triggered", {"motivo": "Jugador presionó ¡BASTA!"}, room=codigo)
        threading.Thread(target=conteo_final, args=(codigo,)).start()
    else:
        print(f"⚠️ ¡BASTA! ignorado: ya había sido activado para sala {codigo}")


@socketio.on("rejoin_room_event")
def handle_rejoin(data):
    codigo = data.get("codigo")
    jugador = data.get("jugador")
    
    # Validar que el jugador tenga un nombre válido
    if not jugador or jugador == "null" or jugador == "undefined" or str(jugador).strip() == "" or str(jugador).lower() == "none":
        print(f"⚠️ Intento de reconexión con nombre inválido: {jugador}")
        return
    
    sid_to_room[request.sid] = codigo
    sid_to_name[request.sid] = jugador
    join_room(codigo)
    
    iniciando_partida.discard(jugador)
    
    print(f"🔄 Jugador {jugador} se reconectó a la sala {codigo}")

    sala = state["salas"].get(codigo, {})
    if sala:
        socketio.emit("restore_state", {
            "letra": sala.get("letra", "?"),
            "tiempo_restante": sala.get("tiempo_restante", 0),
            "ronda": sala.get("ronda_actual", 1),
            "en_curso": sala.get("en_curso", False)
        }, room=request.sid)

@socketio.on("enviar_respuestas")
def handle_enviar_respuestas(data):
    codigo = data.get("codigo")
    jugador = data.get("jugador")
    respuestas = data.get("respuestas")
    
    # Validar que el jugador tenga un nombre válido
    if not jugador or jugador == "null" or jugador == "undefined" or str(jugador).strip() == "" or str(jugador).lower() == "none":
        print(f"⚠️ Intento de enviar respuestas con nombre inválido: {jugador}")
        return
    
    sala = state["salas"].get(codigo)
    if sala and jugador:
        # Verificar si la ronda está pausada
        if sala.get("pausada", False):
            socketio.emit("ronda_pausada", {
                "pausada": True,
                "mensaje": "La ronda está pausada. No puedes enviar respuestas."
            }, room=request.sid)
            return
        
        if "respuestas_ronda" not in sala:
            sala["respuestas_ronda"] = {}
        sala["respuestas_ronda"][jugador] = respuestas
        save_state(state)
        print(f"📋 Respuestas recibidas de {jugador} en sala {codigo}")

def conteo_final(codigo):
    with app.app_context():
        for s in range(5, 0, -1):
            socketio.emit("update_timer", {"tiempo": s, "fase": "basta"}, room=codigo)
            time.sleep(1)
        
        results_packet = calcular_puntuaciones(codigo)
        
        sala = state["salas"].get(codigo)
        if not sala: return

        ronda_actual = sala.get("ronda_actual", 1)
        total_rondas = sala.get("rondas", 1)
        
        fin_del_juego = False
        if ronda_actual >= total_rondas:
            fin_del_juego = True
            sala["en_curso"] = False
            sala["finalizada"] = True  # Marcar partida como finalizada
        else:
            sala["ronda_actual"] = ronda_actual + 1
            sala["en_curso"] = False

        if results_packet:
            results_packet["fin_del_juego"] = fin_del_juego
            
            # Verificar si todos tienen 0 puntos (no hay ganador)
            if fin_del_juego:
                modo_juego = sala.get("modo_juego", "clasico")
                if modo_juego == "equipos" and results_packet.get("puntuaciones_equipos"):
                    # Verificar equipos
                    todas_puntuaciones = list(results_packet["puntuaciones_equipos"].values())
                    results_packet["sin_ganador"] = all(puntos == 0 for puntos in todas_puntuaciones) and len(todas_puntuaciones) > 0
                else:
                    # Verificar jugadores individuales
                    todas_puntuaciones = list(results_packet.get("scores_total", {}).values())
                    results_packet["sin_ganador"] = all(puntos == 0 for puntos in todas_puntuaciones) and len(todas_puntuaciones) > 0
            else:
                results_packet["sin_ganador"] = False
                
            results_packet["proxima_ronda"] = sala.get("ronda_actual")
            
            # Debug: verificar que las validaciones estén en el packet
            print(f"📤 Enviando round_results a sala {codigo}")
            print(f"   • Jugadores: {list(results_packet.get('validaciones_ia', {}).keys())}")
            print(f"   • Validaciones IA incluidas: {len(results_packet.get('validaciones_ia', {}))} jugadores")
            print(f"   • Puntos por respuesta incluidos: {len(results_packet.get('puntos_por_respuesta', {}))} jugadores")
            
            socketio.emit("round_results", results_packet, room=codigo)
            print(f"✅ round_results emitido correctamente")
        
        sala["basta_activado"] = False
        # NO limpiar respuestas_ronda todavía - se necesitan para apelaciones
        # sala["respuestas_ronda"] = {}
        
        # El anfitrión siempre se marca como listo automáticamente
        anfitrion = sala.get("anfitrion")
        sala["jugadores_listos"] = [anfitrion] if anfitrion else []
        
        save_state(state)
        
        # Notificar a todos sobre el estado actualizado de jugadores listos
        socketio.emit("player_joined", {
            "jugadores": sala["jugadores"],
            "puntuaciones": sala.get("puntuaciones", {}),
            "jugadores_listos": sala.get("jugadores_listos", []),
            "jugadores_desconectados": sala.get("jugadores_desconectados", []),
            "configuracion": {
                "rondas": sala.get("rondas", 3),
                "dificultad": sala.get("dificultad", "normal"),
                "modo_juego": sala.get("modo_juego", "clasico"),
                "chat_habilitado": sala.get("chat_habilitado", True),
                "sonidos_habilitados": sala.get("sonidos_habilitados", True),
                "powerups_habilitados": sala.get("powerups_habilitados", True),
                "validacion_activa": sala.get("validacion_activa", True)
            }
        }, room=codigo)
        
        emit_admin_log(f"🏁 Ronda {ronda_actual} terminada", "game", codigo)


# ==========================================================
# EVENTOS DE CHAT EN TIEMPO REAL
# ==========================================================
@socketio.on("enviar_mensaje_chat")
def handle_chat_message(data):
    codigo = data.get("codigo")
    jugador = data.get("jugador")
    mensaje = data.get("mensaje", "").strip()
    
    if not jugador or not mensaje:
        return
    
    sala = state["salas"].get(codigo)
    if not sala:
        return
    
    # Verificar si el chat está habilitado
    if not sala.get("chat_habilitado", True):
        return
    
    # APLICAR FILTROS DE CENSURA CON IA (ahora devuelve 4 valores)
    mensaje_filtrado, es_valido, razon, tiene_groseria = filtrar_mensaje_chat(mensaje, sala, codigo)
    
    # Si el mensaje no es válido, notificar al usuario y rechazar
    if not es_valido:
        socketio.emit("mensaje_rechazado", {
            "razon": razon,
            "mensaje_original": mensaje
        }, room=request.sid)
        print(f"🚫 Mensaje rechazado de {jugador}: {razon}")
        return
    
    # Agregar mensaje FILTRADO al historial
    mensaje_obj = {
        "jugador": jugador,
        "mensaje": mensaje_filtrado,  # Usar mensaje filtrado (censurado)
        "timestamp": time.time(),
        "tipo": "usuario"
    }
    
    if "mensajes_chat" not in sala:
        sala["mensajes_chat"] = []
    
    sala["mensajes_chat"].append(mensaje_obj)
    
    # Limitar a últimos 50 mensajes
    if len(sala["mensajes_chat"]) > 50:
        sala["mensajes_chat"] = sala["mensajes_chat"][-50:]
    
    save_state(state)
    
    # Obtener IP y dispositivo para el log (desde SocketIO)
    ip = get_client_ip_from_environ()
    user_agent = get_user_agent_from_environ()
    dispositivo_info = parse_user_agent(user_agent)
    
    # Emitir mensaje del USUARIO a todos en la sala
    socketio.emit("nuevo_mensaje_chat", mensaje_obj, room=codigo)
    emit_admin_log(f"💬 {jugador}: {mensaje_filtrado[:50]}{'...' if len(mensaje_filtrado) > 50 else ''}", "chat", codigo, ip=ip, dispositivo_info=dispositivo_info)
    
    # Si contenía groserías, enviar MENSAJE DEL SISTEMA por separado (EN ROJO)
    if tiene_groseria:
        mensaje_moderacion = {
            "jugador": "Sistema",
            "mensaje": "⚠️ Mensaje moderado: se detectaron palabras inapropiadas",
            "timestamp": time.time(),
            "tipo": "sistema_moderacion"
        }
        
        sala["mensajes_chat"].append(mensaje_moderacion)
        save_state(state)
        
        socketio.emit("nuevo_mensaje_chat", mensaje_moderacion, room=codigo)
        print(f"⚠️ Moderación aplicada en sala {codigo} al mensaje de {jugador}")


# ==========================================================
# EVENTOS DE POWER-UPS
# ==========================================================
@socketio.on("usar_powerup")
def handle_usar_powerup(data):
    codigo = data.get("codigo")
    jugador = data.get("jugador")
    powerup = data.get("powerup")
    
    sala = state["salas"].get(codigo)
    if not sala or not sala.get("powerups_habilitados", True):
        return
    
    if powerup not in POWERUPS:
        return
    
    # Verificar si el jugador tiene el power-up
    powerups_jugador = sala.get("powerups_jugadores", {}).get(jugador, {})
    
    if powerups_jugador.get(powerup, 0) <= 0:
        emit("powerup_error", {"error": "No tienes este power-up"})
        return
    
    # Usar el power-up
    powerups_jugador[powerup] -= 1
    sala["powerups_jugadores"][jugador] = powerups_jugador
    
    # Aplicar efecto según el tipo
    if powerup == "tiempo_extra":
        tiempo_actual = sala.get("tiempo_restante", 0)
        sala["tiempo_restante"] = tiempo_actual + 30
        socketio.emit("update_timer", {"tiempo": sala["tiempo_restante"]}, room=codigo)
        socketio.emit("powerup_usado", {
            "jugador": jugador,
            "powerup": "tiempo_extra",
            "mensaje": f"{jugador} usó Tiempo Extra! (+30 segundos)"
        }, room=codigo)
    
    elif powerup == "cambiar_letra":
        nueva_letra = random.choice("ABCDEFGHIJKLMNÑOPQRSTUVWXYZ")
        sala["letra"] = nueva_letra
        socketio.emit("letra_cambiada", {
            "letra": nueva_letra,
            "jugador": jugador
        }, room=codigo)
    
    elif powerup == "pista":
        # Dar una pista (primera letra de una respuesta válida)
        categoria = random.choice(sala.get("categorias", []))
        letra = sala.get("letra", "A")
        # Aquí podrías integrar una API o diccionario
        emit("pista_powerup", {
            "categoria": categoria,
            "pista": f"Una palabra que empieza con {letra}"
        })
    
    save_state(state)
    print(f"⚡ {jugador} usó power-up: {powerup}")


@socketio.on("dar_powerup")
def handle_dar_powerup(data):
    """Administrador puede dar power-ups a jugadores"""
    codigo = data.get("codigo")
    jugador_destino = data.get("jugador")
    powerup = data.get("powerup")
    jugador_admin = data.get("admin")
    
    sala = state["salas"].get(codigo)
    if not sala:
        return
    
    # Verificar que es el anfitrión
    if jugador_admin != sala.get("anfitrion"):
        return
    
    if powerup not in POWERUPS:
        return
    
    # Dar el power-up
    if "powerups_jugadores" not in sala:
        sala["powerups_jugadores"] = {}
    
    if jugador_destino not in sala["powerups_jugadores"]:
        sala["powerups_jugadores"][jugador_destino] = {
            "tiempo_extra": 0, "pista": 0, "cambiar_letra": 0, 
            "escudo": 0, "doble_puntos": 0
        }
    
    sala["powerups_jugadores"][jugador_destino][powerup] = \
        sala["powerups_jugadores"][jugador_destino].get(powerup, 0) + 1
    
    save_state(state)
    
    socketio.emit("powerup_recibido", {
        "jugador": jugador_destino,
        "powerup": powerup,
        "cantidad": sala["powerups_jugadores"][jugador_destino][powerup]
    }, room=codigo)


# ==========================================================
# EVENTOS DE VALIDACIÓN
# ==========================================================
@socketio.on("cuestionar_respuesta")
def handle_cuestionar_respuesta(data):
    codigo = data.get("codigo")
    jugador_cuestionado = data.get("jugador_cuestionado")
    categoria = data.get("categoria")
    jugador_que_cuestiona = data.get("jugador")
    
    sala = state["salas"].get(codigo)
    if not sala or not sala.get("validacion_activa", False):
        return
    
    key = f"{jugador_cuestionado}:{categoria}"
    
    if "respuestas_cuestionadas" not in sala:
        sala["respuestas_cuestionadas"] = {}
    
    sala["respuestas_cuestionadas"][key] = {
        "jugador": jugador_cuestionado,
        "categoria": categoria,
        "respuesta": sala["respuestas_ronda"].get(jugador_cuestionado, {}).get(categoria, ""),
        "cuestionada_por": jugador_que_cuestiona,
        "votos_valida": 0,
        "votos_invalida": 0,
        "votantes": []
    }
    
    save_state(state)
    
    # Iniciar votación
    socketio.emit("iniciar_votacion", {
        "jugador": jugador_cuestionado,
        "categoria": categoria,
        "respuesta": sala["respuestas_cuestionadas"][key]["respuesta"]
    }, room=codigo)


@socketio.on("votar_validacion")
def handle_votar_validacion(data):
    codigo = data.get("codigo")
    key = data.get("key")  # "jugador:categoria"
    voto = data.get("voto")  # "valida" o "invalida"
    votante = data.get("votante")
    
    sala = state["salas"].get(codigo)
    if not sala:
        return
    
    cuestion = sala.get("respuestas_cuestionadas", {}).get(key)
    if not cuestion:
        return
    
    # Verificar que no ha votado ya
    if votante in cuestion["votantes"]:
        return
    
    cuestion["votantes"].append(votante)
    
    if voto == "valida":
        cuestion["votos_valida"] += 1
    else:
        cuestion["votos_invalida"] += 1
    
    # Si todos votaron, resolver
    total_jugadores = len(sala["jugadores"])
    if len(cuestion["votantes"]) >= total_jugadores - 1:  # -1 porque el cuestionado no vota
        # Determinar resultado
        if cuestion["votos_invalida"] > cuestion["votos_valida"]:
            # Respuesta invalidada - penalizar
            jugador = cuestion["jugador"]
            categoria = cuestion["categoria"]
            
            # Quitar puntos
            puntos_perdidos = 50
            if jugador in sala["puntuaciones"]:
                sala["puntuaciones"][jugador] = max(0, sala["puntuaciones"][jugador] - puntos_perdidos)
            
            # Agregar penalización
            if "penalizaciones" not in sala:
                sala["penalizaciones"] = {}
            sala["penalizaciones"][jugador] = sala["penalizaciones"].get(jugador, 0) + 1
            
            socketio.emit("respuesta_invalidada", {
                "jugador": jugador,
                "categoria": categoria,
                "puntos_perdidos": puntos_perdidos
            }, room=codigo)
        else:
            socketio.emit("respuesta_validada", {
                "jugador": cuestion["jugador"],
                "categoria": cuestion["categoria"]
            }, room=codigo)
        
        # Limpiar cuestionamiento
        del sala["respuestas_cuestionadas"][key]
    
    save_state(state)


# ==========================================================
# EVENTOS DE APELACIÓN
# ==========================================================
@socketio.on("solicitar_apelacion")
def handle_solicitar_apelacion(data):
    """Maneja cuando un jugador apela una validación de IA que marcó su respuesta como inválida"""
    codigo = data.get("codigo")
    jugador = data.get("jugador")
    categoria = data.get("categoria")
    respuesta = data.get("respuesta")
    
    emit_admin_log(f"⚠️ [APELACIÓN] Solicitud de {jugador}", "apelacion", codigo)
    print(f"   → Categoría: {categoria}, Respuesta: '{respuesta}'")
    
    sala = state["salas"].get(codigo)
    if not sala:
        print(f"❌ Sala {codigo} no encontrada")
        return
    
    # Crear key única para la apelación
    key = f"{jugador}:{categoria}"
    
    if "apelaciones" not in sala:
        sala["apelaciones"] = {}
    
    # Registrar la apelación
    sala["apelaciones"][key] = {
        "jugador": jugador,
        "categoria": categoria,
        "respuesta": respuesta,
        "votos_valida": 0,
        "votos_invalida": 0,
        "votantes": []
    }
    
    save_state(state)
    emit_admin_log(f"✅ Apelación registrada: {key}", "apelacion", codigo)
    
    # Notificar a todos los jugadores que hay una nueva apelación para votar
    socketio.emit("iniciar_votacion_apelacion", {
        "jugador": jugador,
        "categoria": categoria,
        "respuesta": respuesta
    }, room=codigo)
    emit_admin_log(f"📤 Votación de apelación iniciada", "apelacion", codigo)


@socketio.on("votar_apelacion")
def handle_votar_apelacion(data):
    """Maneja los votos de los jugadores sobre una apelación"""
    codigo = data.get("codigo")
    key = data.get("key")  # "jugador:categoria"
    voto = data.get("voto")  # "valida" o "invalida"
    votante = data.get("votante")
    
    emit_admin_log(f"🗳️ [VOTO] {votante} vota '{voto}' en {key}", "apelacion", codigo)
    
    sala = state["salas"].get(codigo)
    if not sala:
        print(f"❌ Sala {codigo} no encontrada")
        return
    
    apelacion = sala.get("apelaciones", {}).get(key)
    if not apelacion:
        print(f"❌ Apelación {key} no encontrada")
        return
    
    # El jugador que apeló no puede votar su propia apelación
    if votante == apelacion["jugador"]:
        print(f"⚠️ {votante} intentó votar su propia apelación")
        return
    
    # Verificar que no ha votado ya
    if votante in apelacion["votantes"]:
        print(f"⚠️ {votante} ya había votado")
        return
    
    apelacion["votantes"].append(votante)
    
    if voto == "valida":
        apelacion["votos_valida"] += 1
    else:
        apelacion["votos_invalida"] += 1
    
    print(f"   Votos actuales: ✓ {apelacion['votos_valida']} | ✗ {apelacion['votos_invalida']} ({len(apelacion['votantes'])}/{len(sala['jugadores'])-1} votos)")
    
    # Si todos votaron (menos el apelante), resolver
    total_jugadores = len(sala["jugadores"])
    if len(apelacion["votantes"]) >= total_jugadores - 1:  # -1 porque el apelante no vota
        emit_admin_log(f"📊 [APELACIÓN] Todos votaron. Resolviendo...", "apelacion", codigo)
        # Determinar resultado por mayoría
        if apelacion["votos_valida"] > apelacion["votos_invalida"]:
            emit_admin_log(f"✅ [APELACIÓN ACEPTADA] ✓ {apelacion['votos_valida']} > ✗ {apelacion['votos_invalida']}", "success", codigo)
            # Apelación aceptada - cambiar validación IA a válida
            jugador_apelado = apelacion["jugador"]
            categoria = apelacion["categoria"]
            
            # Actualizar la validación IA en la sala (mantener formato correcto)
            if "validaciones_ia" not in sala:
                sala["validaciones_ia"] = {}
            
            if jugador_apelado not in sala["validaciones_ia"]:
                sala["validaciones_ia"][jugador_apelado] = {}
            
            # Marcar como válida y agregar razón
            sala["validaciones_ia"][jugador_apelado][categoria] = {
                "validada_ia": True,
                "razon_ia": "Apelación aceptada por votación de jugadores",
                "confianza": 1.0,
                "apelable": False
            }
            
            # Calcular y dar puntos
            respuestas_ronda = sala.get("respuestas_ronda", {})
            puntos_ganados = 0
            
            if jugador_apelado in respuestas_ronda:
                respuesta_jugador = respuestas_ronda[jugador_apelado].get(categoria, "")
                letra = sala.get("letra", "?").upper()
                
                if respuesta_jugador and respuesta_jugador.strip().upper().startswith(letra):
                    # Contar cuántos jugadores tienen la misma respuesta VÁLIDA (validada por IA o apelación)
                    count = 0
                    for j, respuestas in respuestas_ronda.items():
                        otra_respuesta = respuestas.get(categoria, "")
                        if otra_respuesta.strip().upper() == respuesta_jugador.strip().upper():
                            # Verificar si esta respuesta está validada (usando el formato correcto)
                            otra_validacion = sala["validaciones_ia"].get(j, {}).get(categoria, {})
                            if otra_validacion.get("validada_ia", False):
                                count += 1
                    
                    print(f"📊 Conteo para '{respuesta_jugador}': {count} jugador(es) con respuesta válida")
                    
                    # Obtener configuración de puntos
                    dificultad = sala.get("dificultad", "normal")
                    config = DIFICULTADES.get(dificultad, DIFICULTADES["normal"])
                    modo_juego = sala.get("modo_juego", "clasico")
                    
                    # Aplicar multiplicadores según el modo
                    multiplicador = 1.0
                    if modo_juego == "rapido":
                        multiplicador = 1.5
                    elif modo_juego == "duelo":
                        multiplicador = 2.0
                    
                    if count == 1:
                        puntos_ganados = int(config["puntos_unico"] * multiplicador)
                    else:
                        puntos_ganados = int(config["puntos_duplicado"] * multiplicador)
                    
                    # Agregar puntos
                    if jugador_apelado not in sala["puntuaciones"]:
                        sala["puntuaciones"][jugador_apelado] = 0
                    sala["puntuaciones"][jugador_apelado] += puntos_ganados
                    
                    # Actualizar también los puntos de ronda para este jugador
                    # Necesitamos recalcular los puntos de ronda considerando la apelación aceptada
                    if "puntos_ronda_actual" not in sala:
                        sala["puntos_ronda_actual"] = {}
                    if jugador_apelado not in sala["puntos_ronda_actual"]:
                        sala["puntos_ronda_actual"][jugador_apelado] = 0
                    sala["puntos_ronda_actual"][jugador_apelado] += puntos_ganados
                    
                    print(f"💰 Puntos agregados: {puntos_ganados} pts. Nueva puntuación: {sala['puntuaciones'][jugador_apelado]}")
            
            # Calcular puntos de ronda actualizados para todos los jugadores
            # Recalcular los puntos de ronda basándose en las validaciones actuales
            puntos_ronda_actualizados = {}
            respuestas_ronda = sala.get("respuestas_ronda", {})
            validaciones_ia = sala.get("validaciones_ia", {})
            letra = sala.get("letra", "?").upper()
            
            for j in sala.get("jugadores", []):
                puntos_ronda_jugador = 0
                if j in respuestas_ronda:
                    for cat, resp in respuestas_ronda[j].items():
                        if resp and resp.strip().upper().startswith(letra):
                            validacion = validaciones_ia.get(j, {}).get(cat, {})
                            if validacion.get("validada_ia", False):
                                # Contar cuántos tienen la misma respuesta
                                count = sum(1 for otro_j, otras_resp in respuestas_ronda.items() 
                                          if otras_resp.get(cat, "").strip().upper() == resp.strip().upper() 
                                          and validaciones_ia.get(otro_j, {}).get(cat, {}).get("validada_ia", False))
                                
                                dificultad = sala.get("dificultad", "normal")
                                config = DIFICULTADES.get(dificultad, DIFICULTADES["normal"])
                                modo_juego = sala.get("modo_juego", "clasico")
                                multiplicador = 1.0
                                if modo_juego == "rapido":
                                    multiplicador = 1.5
                                elif modo_juego == "duelo":
                                    multiplicador = 2.0
                                
                                if count == 1:
                                    puntos_ronda_jugador += int(config["puntos_unico"] * multiplicador)
                                else:
                                    puntos_ronda_jugador += int(config["puntos_duplicado"] * multiplicador)
                puntos_ronda_actualizados[j] = puntos_ronda_jugador
            
            # Emitir apelación aceptada con puntos ganados Y puntuaciones totales y de ronda actualizadas
            socketio.emit("apelacion_aceptada", {
                "jugador": jugador_apelado,
                "categoria": categoria,
                "respuesta": apelacion["respuesta"],
                "puntos_ganados": puntos_ganados,
                "nueva_puntuacion": sala["puntuaciones"].get(jugador_apelado, 0),
                "puntuaciones_totales": sala["puntuaciones"],  # Enviar todas las puntuaciones actualizadas
                "puntuaciones_ronda": puntos_ronda_actualizados  # Enviar puntos de ronda actualizados
            }, room=codigo)
            print(f"📤 Evento apelacion_aceptada emitido a sala {codigo}")
        else:
            emit_admin_log(f"❌ [APELACIÓN RECHAZADA] ✓ {apelacion['votos_valida']} ≤ ✗ {apelacion['votos_invalida']}", "error", codigo)
            # Apelación rechazada - mantener como inválida
            socketio.emit("apelacion_rechazada", {
                "jugador": apelacion["jugador"],
                "categoria": apelacion["categoria"],
                "respuesta": apelacion["respuesta"]
            }, room=codigo)
        
        # Limpiar apelación
        del sala["apelaciones"][key]
    
    save_state(state)


# ==========================================================
# EVENTOS DE PENALIZACIONES
# ==========================================================
@socketio.on("aplicar_penalizacion")
def handle_aplicar_penalizacion(data):
    """El anfitrión puede aplicar penalizaciones manuales"""
    codigo = data.get("codigo")
    jugador_penalizado = data.get("jugador")
    razon = data.get("razon", "Conducta inapropiada")
    admin = data.get("admin")
    
    sala = state["salas"].get(codigo)
    if not sala:
        return
    
    # Verificar que es el anfitrión
    if admin != sala.get("anfitrion"):
        return
    
    if "penalizaciones" not in sala:
        sala["penalizaciones"] = {}
    
    sala["penalizaciones"][jugador_penalizado] = \
        sala["penalizaciones"].get(jugador_penalizado, 0) + 1
    
    # Quitar 100 puntos
    if jugador_penalizado in sala["puntuaciones"]:
        sala["puntuaciones"][jugador_penalizado] = \
            max(0, sala["puntuaciones"][jugador_penalizado] - 100)
    
    save_state(state)
    
    socketio.emit("jugador_penalizado", {
        "jugador": jugador_penalizado,
        "razon": razon,
        "penalizaciones_totales": sala["penalizaciones"][jugador_penalizado]
    }, room=codigo)


# ==========================================================
# RUTAS DE API ADICIONALES
# ==========================================================
@app.route("/api/categorias", methods=["GET"])
def get_categorias():
    """Obtener todas las categorías disponibles"""
    return jsonify({
        "ok": True,
        "categorias": CATEGORIAS_DISPONIBLES
    })


@app.route("/api/dificultades", methods=["GET"])
def get_dificultades():
    """Obtener configuración de dificultades"""
    return jsonify({
        "ok": True,
        "dificultades": DIFICULTADES
    })


@app.route("/api/modos", methods=["GET"])
def get_modos():
    """Obtener modos de juego disponibles"""
    return jsonify({
        "ok": True,
        "modos": MODOS_JUEGO
    })


@app.route("/api/powerups", methods=["GET"])
def get_powerups():
    """Obtener power-ups disponibles"""
    return jsonify({
        "ok": True,
        "powerups": POWERUPS
    })


# ==========================================================
# PANEL DE SUPER ADMINISTRACIÓN (SOLO PARA EL DESARROLLADOR)
# ==========================================================
ADMIN_PASSWORD = "SOIIM0UCABW#$%" # Cambia esto por tu contraseña
ADMIN_SECRET_KEY = os.getenv("ADMIN_SECRET_KEY", "basta_admin_secret_2025_change_this")  # Cambia esto también
ADMIN_SESSION_DURATION = 3600  # 1 hora en segundos

# Sistema de seguridad: Rate limiting y bloqueo de IPs
admin_login_attempts = {}  # {ip: {"count": int, "blocked_until": datetime}}
MAX_LOGIN_ATTEMPTS = 5
BLOCK_DURATION_MINUTES = 30

def hash_password(password):
    """Hash seguro de contraseña usando SHA-256 con salt"""
    salt = ADMIN_SECRET_KEY.encode()
    return hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 100000).hex()

def generate_admin_token():
    """Genera un token seguro para la sesión de admin"""
    timestamp = str(int(time.time()))
    random_part = ''.join(random.choices(string.ascii_letters + string.digits, k=32))
    token_data = f"{timestamp}:{random_part}"
    signature = hmac.new(
        ADMIN_SECRET_KEY.encode(),
        token_data.encode(),
        hashlib.sha256
    ).hexdigest()
    token = base64.b64encode(f"{token_data}:{signature}".encode()).decode()
    return token

def verify_admin_token(token):
    """Verifica que el token de admin sea válido"""
    try:
        decoded = base64.b64decode(token.encode()).decode()
        token_data, signature = decoded.rsplit(':', 1)
        timestamp, random_part = token_data.split(':', 1)
        
        # Verificar que el token no sea muy viejo (máximo 1 hora)
        token_time = int(timestamp)
        if time.time() - token_time > ADMIN_SESSION_DURATION:
            return False
        
        # Verificar firma
        expected_signature = hmac.new(
            ADMIN_SECRET_KEY.encode(),
            token_data.encode(),
            hashlib.sha256
        ).hexdigest()
        
        return hmac.compare_digest(signature, expected_signature)
    except:
        return False

def get_client_ip():
    """Obtiene la IP real del cliente"""
    if request.headers.get('X-Forwarded-For'):
        return request.headers.get('X-Forwarded-For').split(',')[0].strip()
    return request.remote_addr

def get_client_ip_from_environ():
    """Obtiene la IP del cliente desde request.environ (útil para SocketIO)"""
    try:
        if 'HTTP_X_FORWARDED_FOR' in request.environ:
            return request.environ['HTTP_X_FORWARDED_FOR'].split(',')[0].strip()
        return request.environ.get('REMOTE_ADDR', 'Desconocido')
    except:
        return 'Desconocido'

def get_user_agent_from_environ():
    """Obtiene el User-Agent desde request.environ (útil para SocketIO)"""
    try:
        return request.environ.get('HTTP_USER_AGENT', '')
    except:
        return ''

def check_ip_blocked(ip):
    """Verifica si una IP está bloqueada"""
    if ip not in admin_login_attempts:
        return False
    
    attempt_data = admin_login_attempts[ip]
    if "blocked_until" in attempt_data:
        if datetime.now() < attempt_data["blocked_until"]:
            return True
        else:
            # Desbloquear si ya pasó el tiempo
            del admin_login_attempts[ip]
            return False
    return False

def record_failed_attempt(ip):
    """Registra un intento fallido de login"""
    if ip not in admin_login_attempts:
        admin_login_attempts[ip] = {"count": 0}
    
    admin_login_attempts[ip]["count"] += 1
    
    if admin_login_attempts[ip]["count"] >= MAX_LOGIN_ATTEMPTS:
        admin_login_attempts[ip]["blocked_until"] = datetime.now() + timedelta(minutes=BLOCK_DURATION_MINUTES)
        print(f"🚫 IP {ip} bloqueada por {BLOCK_DURATION_MINUTES} minutos después de {MAX_LOGIN_ATTEMPTS} intentos fallidos")

def reset_attempts(ip):
    """Resetea los intentos fallidos para una IP"""
    if ip in admin_login_attempts:
        del admin_login_attempts[ip]

def require_admin_auth(f):
    """Decorador para requerir autenticación de admin"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        token = request.cookies.get("admin_token")
        if not token or not verify_admin_token(token):
            return jsonify({"ok": False, "error": "No autorizado"}), 403
        return f(*args, **kwargs)
    return decorated_function

# Hash de la contraseña al iniciar (solo se calcula una vez)
ADMIN_PASSWORD_HASH = hash_password(ADMIN_PASSWORD)

@app.route("/admin")
def admin_panel():
    """Panel de administración del sistema"""
    # Verificar si ya está autenticado
    token = request.cookies.get("admin_token")
    is_authenticated = token and verify_admin_token(token)
    
    if not is_authenticated:
        # Mostrar página de login
        return render_template("admin_login.html")
    
    # Mostrar dashboard de administración
    return render_template("admin_dashboard.html")


@app.route("/admin/login", methods=["POST"])
def admin_login():
    """Autenticar como administrador con seguridad mejorada"""
    client_ip = get_client_ip()
    
    # Verificar si la IP está bloqueada
    if check_ip_blocked(client_ip):
        remaining_time = (admin_login_attempts[client_ip]["blocked_until"] - datetime.now()).total_seconds() / 60
        return jsonify({
            "ok": False, 
            "error": f"IP bloqueada. Intenta de nuevo en {int(remaining_time)} minutos"
        }), 403
    
    data = request.get_json() if request.is_json else request.form.to_dict()
    password = data.get("password", "")
    
    # Validar entrada
    if not password or len(password) < 3:
        record_failed_attempt(client_ip)
        return jsonify({"ok": False, "error": "Contraseña incorrecta"}), 403
    
    # Verificar contraseña usando hash
    password_hash = hash_password(password)
    
    if hmac.compare_digest(password_hash, ADMIN_PASSWORD_HASH):
        # Login exitoso
        reset_attempts(client_ip)
        token = generate_admin_token()
        response = jsonify({"ok": True, "message": "Autenticación exitosa"})
        response.set_cookie(
            "admin_token", 
            token, 
            max_age=ADMIN_SESSION_DURATION,
            httponly=True,  # Prevenir acceso desde JavaScript
            secure=False,  # Cambiar a True en producción con HTTPS
            samesite='Lax'  # Protección CSRF
        )
        print(f"✅ [ADMIN] Login exitoso desde IP: {client_ip}")
        return response
    else:
        # Login fallido
        record_failed_attempt(client_ip)
        attempts_left = MAX_LOGIN_ATTEMPTS - admin_login_attempts.get(client_ip, {}).get("count", 0)
        print(f"⚠️ [ADMIN] Intento de login fallido desde IP: {client_ip} (Intentos restantes: {attempts_left})")
        
        if attempts_left <= 0:
            return jsonify({
                "ok": False, 
                "error": f"Demasiados intentos fallidos. IP bloqueada por {BLOCK_DURATION_MINUTES} minutos"
            }), 403
        
        return jsonify({
            "ok": False, 
            "error": f"Contraseña incorrecta. Intentos restantes: {attempts_left}"
        }), 403


@app.route("/admin/logout")
def admin_logout():
    """Cerrar sesión de administrador"""
    response = redirect("/")
    response.set_cookie("admin_token", "", max_age=0, httponly=True)
    return response


@app.route("/api/admin/salas", methods=["GET"])
@require_admin_auth
def get_all_salas():
    """Obtener todas las salas activas (solo admin)"""
    
    salas_info = []
    for codigo, sala in state["salas"].items():
        # Filtrar jugadores desconectados de la lista
        jugadores_activos = [
            j for j in sala.get("jugadores", []) 
            if j not in sala.get("jugadores_desconectados", [])
        ]
        
        salas_info.append({
            "codigo": codigo,
            "anfitrion": sala.get("anfitrion"),
            "jugadores": jugadores_activos,  # Solo jugadores activos
            "estado": sala.get("estado", "espera"),
            "ronda_actual": sala.get("ronda_actual", 1),
            "total_rondas": sala.get("rondas", 1),
            "modo_juego": sala.get("modo_juego", "clasico"),
            "en_curso": sala.get("en_curso", False),
            "pausada": sala.get("pausada", False),  # Estado de pausa
            "num_mensajes": len(sala.get("mensajes_chat", []))
        })
    
    return jsonify({
        "ok": True,
        "salas": salas_info,
        "total_salas": len(salas_info)
    })


@app.route("/api/admin/sala/<codigo>", methods=["GET"])
@require_admin_auth
def get_sala_completa(codigo):
    """Obtener configuración completa de una sala (solo admin)"""
    
    sala = state["salas"].get(codigo)
    if not sala:
        return jsonify({"ok": False, "error": "Sala no encontrada"}), 404
    
    # Obtener IDs de jugadores
    jugadores_con_ids = []
    jugadores_ids = sala.get("jugadores_ids", {})
    for jugador in sala.get("jugadores", []):
        player_id = jugadores_ids.get(jugador, "N/A")
        jugadores_con_ids.append({
            "nombre": jugador,
            "player_id": player_id
        })
    
    return jsonify({
        "ok": True,
        "sala": {
            "codigo": codigo,
            "anfitrion": sala.get("anfitrion"),
            "jugadores": sala.get("jugadores", []),
            "jugadores_con_ids": jugadores_con_ids,
            "powerups_habilitados": sala.get("powerups_habilitados", True),
            "chat_habilitado": sala.get("chat_habilitado", True),
            "sonidos_habilitados": sala.get("sonidos_habilitados", True),
            "validacion_activa": sala.get("validacion_activa", True),
            "pausada": sala.get("pausada", False),
            "en_curso": sala.get("en_curso", False)
        }
    })


@app.route("/api/admin/sala/<codigo>/chat", methods=["GET"])
@require_admin_auth
def get_sala_chat(codigo):
    """Obtener todos los mensajes de chat de una sala (solo admin)"""
    
    sala = state["salas"].get(codigo)
    if not sala:
        return jsonify({"ok": False, "error": "Sala no encontrada"}), 404
    
    return jsonify({
        "ok": True,
        "codigo": codigo,
        "mensajes": sala.get("mensajes_chat", []),
        "anfitrion": sala.get("anfitrion")
    })


@app.route("/api/admin/cambiar_config", methods=["POST"])
@require_admin_auth
def cambiar_config_sala():
    """Cambiar configuración de una sala (solo admin)"""
    
    data = request.get_json()
    codigo = data.get("codigo")
    feature = data.get("feature")
    value = data.get("value")
    
    sala = state["salas"].get(codigo)
    if not sala:
        return jsonify({"ok": False, "error": "Sala no encontrada"}), 404
    
    # Actualizar configuración
    sala[feature] = value
    save_state(state)
    
    # Notificar a todos los jugadores de la sala
    socketio.emit("configuracion_actualizada", {
        "powerups_habilitados": sala.get("powerups_habilitados", True),
        "chat_habilitado": sala.get("chat_habilitado", True),
        "sonidos_habilitados": sala.get("sonidos_habilitados", True),
        "validacion_activa": sala.get("validacion_activa", True)
    }, room=codigo)
    
    print(f"⚙️ [ADMIN] Configuración actualizada en sala {codigo}: {feature} = {value}")
    
    return jsonify({
        "ok": True,
        "message": "Configuración actualizada"
    })


@app.route("/api/admin/estadisticas", methods=["GET"])
@require_admin_auth
def get_estadisticas():
    """Obtener estadísticas del sistema (solo admin)"""
    
    # Limpiar sid_to_name de entradas inválidas (sockets desconectados)
    # Contar solo jugadores realmente conectados (con sockets activos y en salas válidas)
    jugadores_unicos = set()
    for sid, nombre in sid_to_name.items():
        codigo = sid_to_room.get(sid)
        if codigo and codigo in state["salas"]:
            sala = state["salas"][codigo]
            # Solo contar si el jugador está en la lista de jugadores de la sala
            # y no está en la lista de desconectados
            if nombre in sala.get("jugadores", []) and nombre not in sala.get("jugadores_desconectados", []):
                jugadores_unicos.add(nombre)
    
    jugadores_conectados = len(jugadores_unicos)
    
    salas_activas = sum(1 for sala in state["salas"].values() if sala.get("en_curso", False))
    total_mensajes = sum(len(sala.get("mensajes_chat", [])) for sala in state["salas"].values())
    
    return jsonify({
        "ok": True,
        "estadisticas": {
            "total_salas": len(state["salas"]),
            "salas_activas": salas_activas,
            "salas_en_espera": len(state["salas"]) - salas_activas,
            "total_jugadores": jugadores_conectados,  # Solo jugadores realmente conectados
            "total_mensajes": total_mensajes
        }
    })

@app.route("/api/admin/sala/<codigo>/pausar", methods=["POST"])
@require_admin_auth
def pausar_ronda(codigo):
    """Pausar/despausar una ronda en curso (solo admin)"""
    sala = state["salas"].get(codigo)
    if not sala:
        return jsonify({"ok": False, "error": "Sala no encontrada"}), 404
    
    if not sala.get("en_curso", False):
        return jsonify({"ok": False, "error": "No hay ronda en curso"}), 400
    
    # Cambiar estado de pausa
    pausada = not sala.get("pausada", False)
    sala["pausada"] = pausada
    save_state(state)
    
    # Notificar a todos los jugadores
    socketio.emit("ronda_pausada", {
        "pausada": pausada,
        "mensaje": "Ronda pausada por administrador" if pausada else "Ronda reanudada"
    }, room=codigo)
    
    print(f"⏸️ [ADMIN] Ronda {'pausada' if pausada else 'reanudada'} en sala {codigo}")
    
    return jsonify({
        "ok": True,
        "pausada": pausada,
        "message": f"Ronda {'pausada' if pausada else 'reanudada'} correctamente"
    })

@app.route("/api/admin/sala/<codigo>/respuestas", methods=["GET"])
@require_admin_auth
def get_respuestas_sala(codigo):
    """Obtener todas las respuestas de los jugadores en una ronda (solo admin)"""
    sala = state["salas"].get(codigo)
    if not sala:
        return jsonify({"ok": False, "error": "Sala no encontrada"}), 404
    
    respuestas_ronda = sala.get("respuestas_ronda", {})
    jugadores_ids = sala.get("jugadores_ids", {})
    
    # Agregar IDs a las respuestas
    respuestas_con_ids = {}
    for jugador, respuestas in respuestas_ronda.items():
        player_id = jugadores_ids.get(jugador, "N/A")
        respuestas_con_ids[jugador] = {
            "player_id": player_id,
            "respuestas": respuestas
        }
    
    return jsonify({
        "ok": True,
        "codigo": codigo,
        "letra": sala.get("letra", "?"),
        "ronda": sala.get("ronda_actual", 1),
        "respuestas": respuestas_con_ids,
        "jugadores_sin_respuestas": [
            j for j in sala.get("jugadores", []) 
            if j not in respuestas_ronda
        ]
    })

@app.route("/api/admin/sala/<codigo>/expulsar", methods=["POST"])
@require_admin_auth
def expulsar_jugador(codigo):
    """Expulsar un jugador de una sala (solo admin)"""
    data = request.get_json()
    player_id = data.get("player_id")
    
    sala = state["salas"].get(codigo)
    if not sala:
        return jsonify({"ok": False, "error": "Sala no encontrada"}), 404
    
    # Verificar que la ronda esté pausada
    if not sala.get("pausada", False):
        return jsonify({"ok": False, "error": "Solo se puede expulsar cuando la ronda está pausada"}), 400
    
    # Obtener nombre del jugador desde el ID
    ids_jugadores = sala.get("ids_jugadores", {})
    jugador = ids_jugadores.get(player_id)
    
    if not jugador:
        return jsonify({"ok": False, "error": "Jugador no encontrado"}), 404
    
    # Obtener todos los sockets del jugador
    sids = player_id_to_sid.get(player_id, [])
    
    # Desconectar todos los sockets del jugador
    for sid in sids:
        if sid in sid_to_room:
            del sid_to_room[sid]
        if sid in sid_to_name:
            del sid_to_name[sid]
        if sid in sid_to_player_id:
            del sid_to_player_id[sid]
        # Desconectar el socket
        try:
            socketio.server.disconnect(sid, namespace='/')
        except:
            pass  # El socket puede ya estar desconectado
    
    # Limpiar mapeos
    if player_id in player_id_to_sid:
        del player_id_to_sid[player_id]
    
    # Remover de la sala
    if jugador in sala["jugadores"]:
        sala["jugadores"].remove(jugador)
    
    if jugador in sala.get("jugadores_listos", []):
        sala["jugadores_listos"].remove(jugador)
    
    # Notificar a todos
    socketio.emit("jugador_expulsado", {
        "jugador": jugador,
        "player_id": player_id,
        "mensaje": f"El jugador {jugador} ha sido expulsado por el administrador"
    }, room=codigo)
    
    save_state(state)
    print(f"🚫 [ADMIN] Jugador {jugador} (ID: {player_id}) expulsado de sala {codigo}")
    
    return jsonify({
        "ok": True,
        "message": f"Jugador {jugador} expulsado correctamente"
    })


# ==========================================================
# ENDPOINTS DE FAILOVER Y HEALTH CHECK
# ==========================================================
@app.route('/health', methods=['GET'])
def health_check():
    """Health check para Azure Front Door"""
    crash_lock_path = "crash.lock"
    if os.path.exists(crash_lock_path):
        return jsonify({"status": "unhealthy"}), 500
    return jsonify({"status": "healthy"}), 200

@app.route('/admin/crash', methods=['POST'])
def simulate_crash():
    """Simula una caída del servidor creando crash.lock"""
    try:
        with open("crash.lock", "w") as f:
            f.write("crash")
        return jsonify({"ok": True, "message": "Crash simulado"}), 200
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route('/admin/recover', methods=['POST'])
def recover():
    """Elimina crash.lock y recarga el estado"""
    try:
        crash_lock_path = "crash.lock"
        if os.path.exists(crash_lock_path):
            os.remove(crash_lock_path)
        global state
        state = load_state()
        return jsonify({"ok": True, "message": "Recuperación exitosa"}), 200
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

# ==========================================================
# EJECUCIÓN LOCAL
# ==========================================================
if __name__ == "__main__":
    print("🚀 Servidor Flask-SocketIO ejecutándose con Gevent en http://127.0.0.1:8081")
    socketio.run(app, host="0.0.0.0", port=8081, debug=True, use_reloader=False)