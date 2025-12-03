from gevent import monkey
monkey.patch_all()
from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from flask_socketio import SocketIO, join_room, emit
import random, string, json, os, threading, time, hashlib, hmac, base64, re, unicodedata
from datetime import datetime, timedelta
from functools import wraps

from database import db, init_db, SalaDB

# Importar OpenAI para validación con IA
try:
    from openai import OpenAI
    from dotenv import load_dotenv
    import os # Aseguramos que os esté disponible aquí
    load_dotenv()
    
    print(f"🔍 DEBUG: Buscando API Key...")
    key = os.getenv("OPENAI_API_KEY")
    
    if not key:
        print("❌ DEBUG: La variable OPENAI_API_KEY está vacía o es None")
        raise ValueError("API Key no encontrada")
    
    print(f"✅ DEBUG: Clave encontrada (Longitud: {len(key)})")
    
    # Intentar configurar OpenAI
    try:
        openai_client = OpenAI(api_key=key)
        OPENAI_AVAILABLE = True
        print("✅ OpenAI configurado correctamente")
    except Exception as e:
        openai_client = None
        OPENAI_AVAILABLE = False
        print(f"🛑 ERROR AL INICIAR CLIENTE: {str(e)}") # <--- ESTO ES LO QUE NECESITAMOS VER
    
except ImportError:
    OPENAI_AVAILABLE = False
    openai_client = None
    print("⚠️ Instala: pip install openai python-dotenv")
except Exception as e:
    OPENAI_AVAILABLE = False
    openai_client = None
    print(f"⚠️ Error general configurando IA: {e}")


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
# POWER-UPS DISPONIBLES (SIMPLIFICADOS Y FUNCIONALES)
# ==========================================================
POWERUPS = {
    "tiempo_extra": {
        "nombre": "Tiempo Extra", 
        "descripcion": "+30 segundos en la ronda", 
        "icon": "⏰", 
        "se_gana_con": "3 respuestas únicas"
    },
    "pista_ia": {
        "nombre": "Pista IA", 
        "descripcion": "IA sugiere palabra para categoría vacía", 
        "icon": "💡", 
        "se_gana_con": "5 respuestas únicas"
    },
    "multiplicador": {
        "nombre": "Multiplicador x2", 
        "descripcion": "Duplica puntos de próxima ronda", 
        "icon": "💎", 
        "se_gana_con": "respuestas 100% únicas"
    }
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
# SELECCIÓN INTELIGENTE DE LETRAS
# ========================================================== 
def seleccionar_letra_aleatoria(sala):
    """
    Selecciona una letra aleatoria sin repetir las ya usadas en la partida.
    Excluye letras difíciles como Ñ, K, W, X, Y, Z.
    """
    # Letras excluidas por ser muy difíciles o tener pocas soluciones
    letras_excluidas = {'Ñ', 'K', 'W', 'X', 'Y', 'Z', 'Q'}
    
    # Todas las letras del alfabeto español menos las excluidas
    letras_disponibles = set("ABCDEFGHIJLMNOPRSTUVÑ") - letras_excluidas
    
    # Obtener letras ya usadas en esta partida
    letras_usadas = sala.get("letras_usadas", set())
    if not isinstance(letras_usadas, set):
        letras_usadas = set(letras_usadas) if letras_usadas else set()
    
    # Letras que aún no se han usado
    letras_no_usadas = letras_disponibles - letras_usadas
    
    # Si ya se usaron todas las letras, reiniciar el pool
    if not letras_no_usadas:
        print(f"🔄 Se usaron todas las letras disponibles, reiniciando pool...")
        letras_no_usadas = letras_disponibles.copy()
        sala["letras_usadas"] = set()
        letras_usadas = set()
    
    # Seleccionar una letra aleatoria del pool disponible
    letra_seleccionada = random.choice(list(letras_no_usadas))
    
    # Agregar la letra a las usadas
    letras_usadas.add(letra_seleccionada)
    sala["letras_usadas"] = letras_usadas
    
    print(f"🎲 Letra seleccionada: {letra_seleccionada} (Usadas: {len(letras_usadas)}/{len(letras_disponibles)})")
    
    return letra_seleccionada

# ========================================================== 
# GENERAR PROMPT MEJORADO PARA VALIDACIÓN 
# ========================================================== 
def generar_prompt_validacion(respuesta, categoria, letra):
    """
    Genera un prompt mejorado para validación IA con reglas específicas según la categoría
    """
    categoria_lower = categoria.lower()
    
    # Determinar artículo
    categorias_femeninas = [
        "fruta", "profesión", "canción", "marca", "comida", "película", 
        "serie", "universidad", "empresa", "ciudad"
    ]
    articulo = "una" if any(palabra in categoria_lower for palabra in categorias_femeninas) else "un"
    
    # ========================================================== 
    # DEFINICIONES ESTRICTAS POR CATEGORÍA
    # ========================================================== 
    definiciones = {
        # BÁSICAS
        "nombre": {
            "definicion": "nombre propio de PERSONA (nombre de pila) real y usado en algún idioma",
            "ejemplos_si": ["Roberto", "María", "Alejandro", "Sofía", "Ahmed", "Yuki"],
            "ejemplos_no": [
                ("Radio", "es un objeto, no nombre de persona"),
                ("Río", "es un cuerpo de agua"),
                ("Rápido", "es un adjetivo"),
                ("Rugido", "es un sonido"),
            ],
            "requiere_existencia": False,
            "reglas_extra": "Debe ser un nombre que personas reales usen.  NO aceptar objetos, lugares, adjetivos o verbos."
        },
        "animal": {
            "definicion": "animal real que existe o existió (incluye extintos como dinosaurios)",
            "ejemplos_si": ["Rinoceronte", "Rana", "Rata", "Tiburón", "Tiranosaurio"],
            "ejemplos_no": [
                ("Río", "es un cuerpo de agua"),
                ("Reloj", "es un objeto"),
                ("Rascacielos", "es un edificio"),
                ("Dragón", "es un animal mitológico/ficticio"),
                ("Unicornio", "es un animal ficticio"),
            ],
            "requiere_existencia": True,
            "reglas_extra": "Debe ser un animal REAL.  NO aceptar animales mitológicos o ficticios (dragones, unicornios, etc.) a menos que existan en la realidad."
        },
        "país o ciudad": {
            "definicion": "país reconocido internacionalmente O ciudad real que existe",
            "ejemplos_si": ["Brasil", "Roma", "Tokio", "Argentina", "Rabat"],
            "ejemplos_no": [
                ("Manzana", "es una fruta"),
                ("Río", "solo es un cuerpo de agua, 'Río de Janeiro' sí sería válido"),
                ("Atlantida", "es una ciudad mitológica"),
                ("NONDON", "mal escrito, sería 'Londres'"),
                ("Perro", "es un animal"),
            ],
            "requiere_existencia": True,
            "reglas_extra": "Debe ser un lugar REAL y existente.  Nombres deben estar correctamente escritos."
        },
        "fruta": {
            "definicion": "fruta real comestible que existe botánicamente",
            "ejemplos_si": ["Manzana", "Rambután", "Frambuesa", "Toronja", "Tamarindo"],
            "ejemplos_no": [
                ("Rascacielos", "es un edificio"),
                ("Brasil", "es un país"),
                ("Rosa", "es una flor, no una fruta"),
                ("Tomate", "botánicamente es fruta pero se acepta"),
                ("Rugido", "es un sonido"),
            ],
            "requiere_existencia": True,
            "reglas_extra": "Debe ser una fruta REAL.  El tomate técnicamente es fruta y se acepta."
        },
        "objeto": {
            "definicion": "objeto físico inanimado, fabricado o creado por humanos",
            "ejemplos_si": ["Reloj", "Radio", "Televisor", "Silla", "Teléfono", "Raqueta"],
            "ejemplos_no": [
                ("Rinoceronte", "es un animal"),
                ("Río", "es un elemento natural, no fabricado"),
                ("Rugido", "es un sonido, no un objeto físico"),
                ("Nariz", "es una parte del cuerpo"),
                ("Árbol", "es un ser vivo natural"),
            ],
            "requiere_existencia": False,
            "reglas_extra": "Debe ser algo FABRICADO/CREADO por humanos. NO partes del cuerpo, animales, plantas o elementos naturales."
        },
        "color": {
            "definicion": "color real y reconocible (incluye tonalidades)",
            "ejemplos_si": ["Rojo", "Rosa", "Rubí", "Turquesa", "Terracota", "Índigo"],
            "ejemplos_no": [
                ("Rugido", "es un sonido"),
                ("Río", "es un cuerpo de agua"),
                ("Rápido", "es un adjetivo de velocidad"),
                ("Reloj", "es un objeto"),
            ],
            "requiere_existencia": False,
            "reglas_extra": "Debe ser un color reconocido. Se aceptan tonalidades y colores menos comunes si son reales."
        },
        
        # INTERMEDIAS
        "profesión": {
            "definicion": "profesión, oficio o trabajo real que personas ejercen",
            "ejemplos_si": ["Médico", "Profesor", "Piloto", "Taxista", "Tornero", "Reportero"],
            "ejemplos_no": [
                ("Mago", "si es de fantasía no, si es ilusionista sí"),
                ("Dragón", "es un animal ficticio"),
                ("Teléfono", "es un objeto"),
                ("Corredor", "depende del contexto - si es atleta sí"),
            ],
            "requiere_existencia": False,
            "reglas_extra": "Debe ser un trabajo REAL que personas ejercen en la vida real."
        },
        "canción": {
            "definicion": "canción real que existe, con título oficial correcto",
            "ejemplos_si": ["Thriller", "Bohemian Rhapsody", "Despacito", "Toxic", "Titanium"],
            "ejemplos_no": [
                ("La Canción Bonita", "título genérico, verificar si existe"),
                ("Música Alegre", "no es un título real"),
                ("Song 12345", "inventado"),
            ],
            "requiere_existencia": True,
            "reglas_extra": "DEBE ser una canción REAL y conocida. El título debe ser el oficial o muy reconocible."
        },
        "artista musical": {
            "definicion": "cantante, banda o grupo musical REAL y verificable",
            "ejemplos_si": ["Tito Doble P", "Taylor Swift", "The Beatles", "Thalía", "Timbiriche", "Twenty One Pilots"],
            "ejemplos_no": [
                ("Los Musicales", "banda inventada"),
                ("DJ Fantasma", "nombre inventado"),
                ("The Super Band", "no existe"),
                ("Cantante Famoso", "no es un nombre de artista"),
            ],
            "requiere_existencia": True,
            "reglas_extra": """CRÍTICO: El artista DEBE existir realmente. 
- La CAPITALIZACIÓN NO IMPORTA ('Tito Doble P' = 'tito doble p' = 'TITO DOBLE P')
- Verificar que sea un artista/banda REAL y conocido
- Aceptar nombres artísticos en cualquier idioma"""
        },
        "videojuego": {
            "definicion": "videojuego REAL con título oficial correcto que existe o existió",
            "ejemplos_si": ["Tetris", "Tekken", "Tomb Raider", "Terraria", "The Last of Us", "Titanfall"],
            "ejemplos_no": [
                ("Trilogy GTA", "título incorrecto, sería 'GTA: The Trilogy'"),
                ("Super Mario 3000", "no existe"),
                ("Call of Duty Zombies War", "título inventado"),
                ("FIFA 2099", "no existe"),
                ("Zelda Adventures", "título incorrecto"),
            ],
            "requiere_existencia": True,
            "reglas_extra": """CRÍTICO: DEBE ser el título OFICIAL o abreviación reconocida.
- NO aceptar títulos con palabras en orden incorrecto
- NO aceptar variaciones inventadas de juegos reales
- 'GTA V' es válido, 'Trilogy GTA' NO es válido"""
        },
        "marca": {
            "definicion": "marca comercial REAL y conocida que existe o existió",
            "ejemplos_si": ["Toyota", "Tesla", "Target", "Tiffany", "TikTok", "Twitch"],
            "ejemplos_no": [
                ("Marcas Buenas", "no es una marca"),
                ("Super Tienda", "nombre genérico"),
                ("TechnoMax", "verificar si existe"),
            ],
            "requiere_existencia": True,
            "reglas_extra": "Debe ser una marca REAL y reconocible a nivel nacional o internacional."
        },
        "comida": {
            "definicion": "platillo, alimento o comida real (preparada o ingrediente)",
            "ejemplos_si": ["Tacos", "Tiramisu", "Tortilla", "Tofu", "Tallarines", "Ternera"],
            "ejemplos_no": [
                ("Brasil", "es un país"),
                ("Teléfono", "es un objeto"),
                ("Tigre", "es un animal, no comida"),
            ],
            "requiere_existencia": True,
            "reglas_extra": "Debe ser algo que se COME.  Incluye platillos, ingredientes, snacks, etc."
        },
        "película": {
            "definicion": "película cinematográfica REAL con título oficial correcto",
            "ejemplos_si": ["Titanic", "Toy Story", "Thor", "Transformers", "Trolls"],
            "ejemplos_no": [
                ("The Movie", "título genérico"),
                ("Película de Acción", "no es un título"),
                ("Avengers 10", "no existe"),
                ("Zootopia Adventures", "no existe"),
            ],
            "requiere_existencia": True,
            "reglas_extra": "DEBE ser una película REAL.  Usar título oficial en cualquier idioma."
        },
        "serie de tv": {
            "definicion": "serie de televisión o streaming REAL que existe o existió",
            "ejemplos_si": ["The Office", "True Detective", "The Crown", "Tuca & Bertie", "Ted Lasso"],
            "ejemplos_no": [
                ("Zootopia Adventures", "no existe, Zootopia es película"),
                ("The Series", "título genérico"),
                ("Netflix Show", "no es un título"),
                ("Breaking Good", "no existe"),
            ],
            "requiere_existencia": True,
            "reglas_extra": "DEBE ser una serie REAL de TV o streaming. NO confundir películas con series."
        },
        
        # DIFÍCILES
        "monumento": {
            "definicion": "monumento, edificio histórico o lugar emblemático REAL",
            "ejemplos_si": ["Torre Eiffel", "Taj Mahal", "Torre de Pisa", "Teotihuacán", "Teatro Colón"],
            "ejemplos_no": [
                ("Brasil", "es un país, no un monumento"),
                ("Edificio Alto", "nombre genérico"),
                ("La Torre", "muy genérico"),
            ],
            "requiere_existencia": True,
            "reglas_extra": "Debe ser un monumento o lugar histórico REAL y reconocible."
        },
        "libro": {
            "definicion": "libro REAL con título oficial correcto",
            "ejemplos_si": ["Twilight", "The Hobbit", "To Kill a Mockingbird", "1984", "The Great Gatsby"],
            "ejemplos_no": [
                ("El Libro Bueno", "título genérico"),
                ("Harry Potter 20", "no existe"),
                ("The Story", "muy genérico"),
            ],
            "requiere_existencia": True,
            "reglas_extra": "DEBE ser un libro REAL publicado.  Usar título oficial."
        },
        "deporte": {
            "definicion": "deporte o actividad deportiva REAL reconocida",
            "ejemplos_si": ["Tenis", "Taekwondo", "Triatlón", "Tiro con arco", "Tubing"],
            "ejemplos_no": [
                ("Correr Rápido", "es una acción, no un deporte con nombre"),
                ("Jugar", "muy genérico"),
                ("Quidditch", "es ficticio de Harry Potter"),
            ],
            "requiere_existencia": True,
            "reglas_extra": "Debe ser un deporte REAL y reconocido oficialmente."
        },
        "evento histórico": {
            "definicion": "evento histórico REAL documentado que ocurrió",
            "ejemplos_si": ["Tratado de Versalles", "Terremoto de 1985", "Toma de la Bastilla", "Titanic hundimiento"],
            "ejemplos_no": [
                ("La Guerra", "muy genérico"),
                ("Evento Importante", "no es específico"),
                ("Batalla de los Dioses", "ficticio"),
            ],
            "requiere_existencia": True,
            "reglas_extra": "DEBE ser un evento REAL de la historia.  Debe ser verificable y documentado."
        },
        "empresa": {
            "definicion": "empresa o compañía REAL que existe o existió",
            "ejemplos_si": ["Tesla", "Toyota", "Twitter", "TikTok", "Telmex", "Televisa"],
            "ejemplos_no": [
                ("Empresa Grande", "nombre genérico"),
                ("Tech Company", "no es un nombre real"),
                ("Super Corp", "verificar si existe"),
            ],
            "requiere_existencia": True,
            "reglas_extra": "DEBE ser una empresa REAL. Similar a marca pero más enfocado en compañías."
        },
        "personaje famoso": {
            "definicion": "persona famosa REAL (celebridad, histórico, deportista, etc.)",
            "ejemplos_si": ["Taylor Swift", "Tom Hanks", "Teresa de Calcuta", "Thatcher Margaret", "Tupac"],
            "ejemplos_no": [
                ("Tony Stark", "es un personaje ficticio de Marvel"),
                ("El Famoso", "no es un nombre"),
                ("Persona Conocida", "no es específico"),
            ],
            "requiere_existencia": True,
            "reglas_extra": "DEBE ser una persona REAL famosa. NO personajes de ficción."
        },
        "universidad": {
            "definicion": "universidad o institución educativa superior REAL",
            "ejemplos_si": ["UNAM", "Universidad de Tokio", "Trinity College", "Tecnológico de Monterrey", "UCLA"],
            "ejemplos_no": [
                ("Universidad Grande", "nombre genérico"),
                ("Escuela de Magia", "ficticia"),
                ("Hogwarts", "ficticia de Harry Potter"),
            ],
            "requiere_existencia": True,
            "reglas_extra": "DEBE ser una universidad REAL que existe o existió."
        },
        "instrumento musical": {
            "definicion": "instrumento musical REAL",
            "ejemplos_si": ["Trompeta", "Tambor", "Triángulo", "Tuba", "Theremin", "Timbal"],
            "ejemplos_no": [
                ("Música", "no es un instrumento"),
                ("Sonido", "no es un instrumento"),
                ("El Instrumento", "muy genérico"),
            ],
            "requiere_existencia": True,
            "reglas_extra": "Debe ser un instrumento REAL que se use para hacer música."
        },
        "superhéroe": {
            "definicion": "superhéroe o superheroína de cómics, películas o series CONOCIDO",
            "ejemplos_si": ["Thor", "Thanos", "Thing (La Cosa)", "Tigra", "Teen Titans"],
            "ejemplos_no": [
                ("Super Hombre Volador", "nombre inventado"),
                ("El Héroe", "muy genérico"),
                ("Captain Fantastico", "verificar si existe"),
            ],
            "requiere_existencia": True,
            "reglas_extra": "DEBE ser un superhéroe REAL de cómics/películas/series conocidas (Marvel, DC, etc.).  NO inventados."
        },
    }
    
    # ========================================================== 
    # OBTENER INFORMACIÓN DE LA CATEGORÍA
    # ========================================================== 
    
    # Buscar la categoría en las definiciones
    info_categoria = None
    for key, value in definiciones.items():
        if key in categoria_lower:
            info_categoria = value
            break
    
    # Si no se encuentra, usar definición genérica
    if not info_categoria:
        info_categoria = {
            "definicion": f"{categoria} real y reconocible",
            "ejemplos_si": [],
            "ejemplos_no": [],
            "requiere_existencia": True,
            "reglas_extra": "Debe ser real y verificable."
        }
    
    # Formatear ejemplos
    ejemplos_validos = ""
    if info_categoria["ejemplos_si"]:
        ejemplos_validos = "EJEMPLOS VÁLIDOS (SI): " + ", ".join(info_categoria["ejemplos_si"])
    
    ejemplos_invalidos = ""
    if info_categoria["ejemplos_no"]:
        ejemplos_invalidos = "EJEMPLOS INVÁLIDOS (NO):\n"
        for ej, razon in info_categoria["ejemplos_no"]:
            ejemplos_invalidos += f'   - "{ej}" → NO ({razon})\n'
    
    verificacion_existencia = ""
    if info_categoria["requiere_existencia"]:
        verificacion_existencia = f"""
⚠️ VERIFICACIÓN DE EXISTENCIA (CRÍTICO):
- "{respuesta}" DEBE EXISTIR en la realidad
- Si NO reconoces que existe o tienes dudas → responde NO
- NO aceptar nombres/títulos inventados, modificados o mal escritos
- Si parece inventado o no lo puedes verificar → NO"""
    
    # ========================================================== 
    # CONSTRUIR PROMPT FINAL
    # ========================================================== 
    
    prompt = f"""Eres un validador ESTRICTO del juego "BASTA/Stop". 

══════════════════════════════════════════════════════════
PREGUNTA: ¿"{respuesta}" es {articulo} {categoria} válido/a que empieza con "{letra}"? 
══════════════════════════════════════════════════════════

DEFINICIÓN DE "{categoria. upper()}": {info_categoria["definicion"]}

{info_categoria["reglas_extra"]}

══════════════════════════════════════════════════════════
PROCESO DE VALIDACIÓN (sigue TODOS los pasos en orden):
══════════════════════════════════════════════════════════

PASO 1 - ¿ES UNA PALABRA/NOMBRE VÁLIDO Y BIEN ESCRITO?
- ¿Está correctamente escrita sin errores ortográficos?
- La CAPITALIZACIÓN NO IMPORTA (ignorar mayúsculas/minúsculas)
- RECHAZA INMEDIATAMENTE si:
  * Parece inventada o sin sentido: "Sasd", "asdas", "Xyzabc"
  * Está mal escrita: "NONDON" (sería Londres), "Mécsico" (sería México)  
  * Es combinación sin sentido: "Nohay", "NOse", "Nomanches"
  * Tiene letras repetidas excesivas: "Holaaaaaa", "Siiiii"

PASO 2 - ¿CORRESPONDE A LA CATEGORÍA "{categoria. upper()}"?
- "{respuesta}" DEBE ser específicamente: {info_categoria["definicion"]}
- NO debe ser otra cosa (país cuando piden fruta, objeto cuando piden animal, etc.)
- Si es claramente OTRA categoría → NO
{verificacion_existencia}

PASO 3 - ¿EMPIEZA CON LA LETRA "{letra. upper()}"?
- La primera letra (ignorando acentos) debe ser "{letra.upper()}"
- Acentos no afectan: "Ángel" empieza con A, "Élefante" empieza con E

══════════════════════════════════════════════════════════
EJEMPLOS PARA "{categoria.upper()}":
══════════════════════════════════════════════════════════
{ejemplos_validos}

{ejemplos_invalidos}

══════════════════════════════════════════════════════════
POLÍTICA: MUY ESTRICTO - ANTE LA DUDA, RECHAZAR
══════════════════════════════════════════════════════════
- Si no estás 100% seguro de que existe → NO
- Si el nombre/título parece modificado o incorrecto → NO
- Si no reconoces que es real → NO
- Si hay CUALQUIER duda → NO
- Es mejor rechazar 10 dudosas que aceptar 1 incorrecta

══════════════════════════════════════════════════════════
RESPUESTA REQUERIDA:
══════════════════════════════════════════════════════════
Responde ÚNICAMENTE en este formato:
"SI - [razón breve]" o "NO - [razón breve]"
"""
    
    return prompt




# ==========================================================
# VALIDACIÓN CON IA (OpenAI) - MEJORADA
# ==========================================================

# Lista de palabras spam/inventadas comunes
PALABRAS_SPAM = {
    "asd", "asdf", "asdas", "sasd", "qwerty", "zxcv", "hjkl", "fghj",
    "nohay", "nose", "nose", "nomanches", "nada", "ninguna", "ninguno",
    "xxx", "zzz", "aaa", "bbb", "test", "prueba", "hola", "chao",
    "jaja", "jeje", "lol", "xd", "wtf", "omg"
}

# Respuestas evasivas o tramposas
RESPUESTAS_EVASIVAS = {
    "no hay", "no se", "no sé", "no existe", "ninguno", "ninguna", 
    "nada", "no aplica", "n/a", "na", "null", "none", "skip"
}

def normalizar_texto(texto):
    """Normaliza texto removiendo acentos para comparaciones"""
    texto_normalizado = unicodedata.normalize('NFD', texto)
    return ''.join(c for c in texto_normalizado if unicodedata. category(c) != 'Mn')

def obtener_primera_letra(texto):
    """Obtiene la primera letra alfabética del texto (sin acentos)"""
    texto_limpio = normalizar_texto(texto. strip())
    for char in texto_limpio:
        if char.isalpha():
            return char. upper()
    return ""

def es_palabra_spam(texto):
    """Detecta si una palabra parece spam o inventada"""
    texto_lower = texto.lower(). strip()
    texto_sin_espacios = texto_lower.replace(" ", "")
    
    # Verificar contra lista de spam
    if texto_sin_espacios in PALABRAS_SPAM:
        return True, "Palabra no válida o spam"
    
    # Verificar respuestas evasivas
    if texto_lower in RESPUESTAS_EVASIVAS:
        return True, "Respuesta evasiva no permitida"
    
    # Detectar caracteres repetidos excesivos (ej: "holaaaaaa", "siiiii")
    if re.search(r'(.)\1{3,}', texto_lower):
        return True, "Caracteres repetidos excesivamente"
    
    # Detectar patrones de teclado (qwerty, asdf, etc.)
    patrones_teclado = ['qwer', 'asdf', 'zxcv', 'qaz', 'wsx', 'edc']
    if any(patron in texto_sin_espacios for patron in patrones_teclado):
        return True, "Patrón de teclado detectado"
    
    return False, ""

def validacion_previa_basica(respuesta, categoria, letra):
    """
    Validación rápida antes de llamar a la IA. 
    Retorna: (debe_rechazar: bool, razon: str) o (False, "") si debe continuar a IA
    """
    
    if not respuesta:
        return True, "Respuesta vacía"
    
    respuesta_limpia = respuesta. strip()
    
    # Muy corta (menos de 2 caracteres)
    if len(respuesta_limpia) < 2:
        return True, "Respuesta demasiado corta"
    
    # Solo espacios o caracteres especiales
    if not any(c. isalpha() for c in respuesta_limpia):
        return True, "Respuesta sin letras válidas"
    
    # Verificar spam/palabras inventadas
    es_spam, razon_spam = es_palabra_spam(respuesta_limpia)
    if es_spam:
        return True, razon_spam
    
    # Verificar que empiece con la letra correcta
    primera_letra = obtener_primera_letra(respuesta_limpia)
    letra_esperada = letra.upper()
    
    if primera_letra != letra_esperada:
        return True, f"No empieza con la letra '{letra_esperada}' (empieza con '{primera_letra}')"
    
    # Detectar solo números
    if respuesta_limpia.isdigit():
        return True, "Solo contiene números"
    
    # Detectar palabras con demasiadas consonantes seguidas (probable inventada)
    # Excepto para palabras extranjeras conocidas
    vocales = set('aeiouáéíóúüAEIOUÁÉÍÓÚÜ')
    max_consonantes_seguidas = 0
    consonantes_actual = 0
    
    for char in respuesta_limpia:
        if char.isalpha() and char not in vocales:
            consonantes_actual += 1
            max_consonantes_seguidas = max(max_consonantes_seguidas, consonantes_actual)
        else:
            consonantes_actual = 0
    
    # 4+ consonantes seguidas es muy raro en español (excepto palabras como "construir")
    if max_consonantes_seguidas >= 5:
        return True, "Patrón de letras no reconocible"
    
    # Pasó validación básica, continuar a IA
    return False, ""


def validar_respuesta_con_ia(respuesta, categoria, letra):
    """
    Valida una respuesta usando IA de OpenAI
    Retorna: (es_valida: bool, razon: str, confianza: float)
    """
    
    # ==========================================================
    # PASO 1: VALIDACIÓN PREVIA (sin IA)
    # ==========================================================
    debe_rechazar, razon_rechazo = validacion_previa_basica(respuesta, categoria, letra)
    
    if debe_rechazar:
        print(f"⛔ Rechazado previamente '{respuesta}': {razon_rechazo}")
        return False, razon_rechazo, 1.0
    
    respuesta_limpia = respuesta. strip()
    
    # ==========================================================
    # PASO 2: VALIDACIÓN CON IA
    # ==========================================================
    if OPENAI_AVAILABLE and openai_client:
        try:
            # Generar prompt optimizado
            prompt = generar_prompt_validacion(respuesta_limpia, categoria, letra)
            
            # Instrucción de sistema clara
            system_prompt = """Eres un validador ESTRICTO del juego BASTA/Stop.
Tu trabajo es verificar si las respuestas son REALES y corresponden a la categoría. 

REGLAS CRÍTICAS:
1. Si NO reconoces que algo existe → responde NO
2. Si el nombre/título parece inventado o modificado → responde NO  
3. Si tienes CUALQUIER duda → responde NO
4. La capitalización NO importa (mayúsculas/minúsculas son equivalentes)
5.  Sé MUY ESTRICTO: es mejor rechazar algo válido que aceptar algo inválido

Responde SOLO con JSON válido, sin texto adicional:
{"valida": true/false, "razon": "explicación breve", "confianza": 0.0-1.0}"""

            response = openai_client. chat.completions.create(
                model="gpt-4o-mini",  # Más preciso que gpt-3.5-turbo para validaciones
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.1,  # Más bajo = más consistente y estricto
                max_tokens=100,
                timeout=8
            )
            
            resultado_texto = response.choices[0].message.content.strip()
            
            # Limpiar respuesta de markdown si viene envuelta
            if "```" in resultado_texto:
                # Extraer contenido entre ```
                match = re.search(r'```(? :json)?\s*(.*? )\s*```', resultado_texto, re.DOTALL)
                if match:
                    resultado_texto = match.group(1)
            
            # Intentar parsear JSON
            try:
                resultado = json.loads(resultado_texto)
            except json.JSONDecodeError:
                # Intentar extraer JSON de texto mixto
                match = re.search(r'\{[^{}]*\}', resultado_texto)
                if match:
                    resultado = json.loads(match.group())
                else:
                    raise ValueError(f"No se pudo extraer JSON de: {resultado_texto}")
            
            es_valida = bool(resultado.get("valida", False))
            razon = str(resultado.get("razon", "Sin razón especificada"))
            confianza = float(resultado.get("confianza", 0.5))
            
            # Asegurar que confianza esté en rango válido
            confianza = max(0.0, min(1.0, confianza))
            
            # Log de resultado
            emoji = "✅" if es_valida else "❌"
            print(f"🤖 IA validó '{respuesta_limpia}' ({categoria}, letra {letra}): {emoji} - {razon} (confianza: {confianza:.0%})")
            
            return es_valida, razon, confianza
            
        except json.JSONDecodeError as e:
            print(f"⚠️ Error parseando JSON de IA: {e}")
            # En caso de error de parsing, ser conservador y rechazar
            return False, "Error de validación - respuesta rechazada por precaución", 0.5
            
        except Exception as e:
            print(f"⚠️ Error en llamada a IA: {type(e).__name__}: {e}")
            # En caso de error de API, ser conservador
            return False, f"Error de validación IA: {str(e)[:50]}", 0.3
    
    # ==========================================================
    # PASO 3: FALLBACK sin IA (muy básico, ser conservador)
    # ==========================================================
    print(f"⚠️ IA no disponible. Validación básica para '{respuesta_limpia}'")
    
    # Sin IA, solo aceptamos si pasa todas las validaciones básicas
    # y rechazamos casos sospechosos
    
    # Verificar longitud mínima razonable
    if len(respuesta_limpia) < 3:
        return False, "Respuesta muy corta (IA no disponible)", 0.5
    
    # Si llegó aquí, aceptar con baja confianza
    return True, "Validación básica (IA no disponible)", 0.4




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

    # 3. Actualizar puntuaciones totales Y OTORGAR POWER-UPS
    puntuaciones_totales = sala.get("puntuaciones", {j: 0 for j in jugadores})
    powerups_ganados = {}  # Registrar power-ups ganados en esta ronda
    
    for jugador, puntos in puntuaciones_ronda.items():
        if jugador not in puntuaciones_totales:
            puntuaciones_totales[jugador] = 0
        
        # Aplicar multiplicador si está activo
        powerups_activos_jugador = sala.get("powerups_activos", {}).get(jugador, [])
        if "multiplicador" in powerups_activos_jugador:
            puntos *= 2
            powerups_activos_jugador.remove("multiplicador")
            print(f"💎 {jugador} usó multiplicador x2 - Puntos: {puntos//2} → {puntos}")
        
        puntuaciones_totales[jugador] += puntos
        
        # ========== OTORGAR POWER-UPS AUTOMÁTICAMENTE ==========
        # Contar respuestas únicas y totales del jugador
        respuestas_jugador = respuestas_por_jugador.get(jugador, {})
        respuestas_unicas = 0
        respuestas_totales = 0
        
        for categoria, respuesta in respuestas_jugador.items():
            respuesta_limpia = respuesta.strip().upper()
            if respuesta_limpia and respuesta_limpia.startswith(letra):
                validacion_jugador = validaciones_ia.get(jugador, {}).get(categoria, {})
                if validacion_jugador.get("validada_ia", False):
                    respuestas_totales += 1
                    lista_respuestas = respuestas_validas_por_categoria.get(categoria, [])
                    if lista_respuestas.count(respuesta_limpia) == 1:
                        respuestas_unicas += 1
        
        # Inicializar powerups del jugador si no existen
        if "powerups_jugadores" not in sala:
            sala["powerups_jugadores"] = {}
        if jugador not in sala["powerups_jugadores"]:
            sala["powerups_jugadores"][jugador] = {"tiempo_extra": 0, "pista_ia": 0, "multiplicador": 0}
        
        powerups_ganados[jugador] = []
        
        # ⏰ TIEMPO EXTRA: 3+ respuestas únicas
        if respuestas_unicas >= 3:
            sala["powerups_jugadores"][jugador]["tiempo_extra"] += 1
            powerups_ganados[jugador].append("tiempo_extra")
            print(f"⏰ {jugador} ganó Tiempo Extra ({respuestas_unicas} respuestas únicas)")
        
        # 💡 PISTA IA: 5+ respuestas únicas
        if respuestas_unicas >= 5:
            sala["powerups_jugadores"][jugador]["pista_ia"] += 1
            powerups_ganados[jugador].append("pista_ia")
            print(f"💡 {jugador} ganó Pista IA ({respuestas_unicas} respuestas únicas)")
        
        # 💎 MULTIPLICADOR: Todas las respuestas únicas (100%)
        if respuestas_totales > 0 and respuestas_unicas == respuestas_totales and respuestas_unicas >= len(sala.get("categorias", [])):
            sala["powerups_jugadores"][jugador]["multiplicador"] += 1
            powerups_ganados[jugador].append("multiplicador")
            print(f"💎 {jugador} ganó Multiplicador x2 (100% respuestas únicas - {respuestas_unicas}/{respuestas_totales})")
        
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
        "powerups_ganados": powerups_ganados,  # Nueva: power-ups ganados en esta ronda
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
            
            # Power-ups de jugadores (simplificados)
            "powerups_jugadores": {nombre: {"tiempo_extra": 0, "pista_ia": 0, "multiplicador": 0}},
            "powerups_activos": {nombre: []},  # Power-ups activos para próxima ronda
            
            # Sistema de validación
            "respuestas_cuestionadas": {},
            "votos_validacion": {},
            
            # Penalizaciones
            "penalizaciones": {nombre: 0},
            
            # Estado de partida
            "finalizada": False,  # Indica si la partida ya finalizó
            "pausada": False,  # Indica si la ronda está pausada
            
            # Control de letras usadas (para evitar repeticiones)
            "letras_usadas": set(),
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


@app.route("/recreate_room", methods=["POST"])
def recreate_room_route():
    """Crear una nueva sala con las mismas configuraciones de una sala finalizada"""
    try:
        data = request.get_json()
        codigo_anterior = data.get("codigo_anterior")
        nombre_anfitrion = data.get("nombre")
        
        sala_anterior = state["salas"].get(codigo_anterior)
        if not sala_anterior:
            return jsonify({"ok": False, "error": "Sala anterior no encontrada"}), 404
        
        # Validar que el nombre sea el anfitrión
        if nombre_anfitrion != sala_anterior.get("anfitrion"):
            return jsonify({"ok": False, "error": "Solo el anfitrión puede recrear la sala"}), 403
        
        # Crear nueva sala con las mismas configuraciones
        codigo_nuevo = generar_codigo()
        
        # Copiar configuraciones de la sala anterior
        global player_id_counter
        player_id_counter += 1
        anfitrion_id = f"P{player_id_counter:06d}"
        
        state["salas"][codigo_nuevo] = {
            "anfitrion": nombre_anfitrion,
            "jugadores": [nombre_anfitrion],
            "rondas": sala_anterior.get("rondas", 3),
            "estado": "espera",
            "puntuaciones": {nombre_anfitrion: 0},
            "respuestas_ronda": {},
            "ronda_actual": 1,
            "jugadores_listos": [nombre_anfitrion],
            "jugadores_desconectados": [],
            "jugadores_ids": {nombre_anfitrion: anfitrion_id},
            "ids_jugadores": {anfitrion_id: nombre_anfitrion},
            "dificultad": sala_anterior.get("dificultad", "normal"),
            "modo_juego": sala_anterior.get("modo_juego", "clasico"),
            "categorias": sala_anterior.get("categorias", []),
            "categorias_personalizadas": sala_anterior.get("categorias_personalizadas"),
            "powerups_habilitados": sala_anterior.get("powerups_habilitados", True),
            "chat_habilitado": sala_anterior.get("chat_habilitado", True),
            "sonidos_habilitados": sala_anterior.get("sonidos_habilitados", True),
            "validacion_activa": sala_anterior.get("validacion_activa", True),
            "equipos": {},
            "puntuaciones_equipos": {},
            "mensajes_chat": [],
            "powerups_jugadores": {nombre_anfitrion: {"tiempo_extra": 0, "pista_ia": 0, "multiplicador": 0}},
            "powerups_activos": {nombre_anfitrion: []},
            "respuestas_cuestionadas": {},
            "votos_validacion": {},
            "penalizaciones": {nombre_anfitrion: 0},
            "finalizada": False,
            "pausada": False,
            "letras_usadas": set(),  # Reiniciar control de letras
            "sala_anterior": codigo_anterior  # Guardar referencia a la sala anterior
        }
        
        save_state(state)
        
        # Guardar mapeo de sala anterior a nueva sala para que otros jugadores puedan unirse
        if "salas_recreadas" not in state:
            state["salas_recreadas"] = {}
        state["salas_recreadas"][codigo_anterior] = codigo_nuevo
        save_state(state)
        
        # Obtener IP y dispositivo para el log
        ip = get_client_ip()
        user_agent = request.headers.get('User-Agent', '')
        dispositivo_info = parse_user_agent(user_agent)
        
        emit_admin_log(f"🔄 Sala recreada | Anfitrión: {nombre_anfitrion} | Sala anterior: {codigo_anterior} → Nueva: {codigo_nuevo}", "info", codigo_nuevo, ip=ip, dispositivo_info=dispositivo_info)
        
        return jsonify({"codigo": codigo_nuevo, "ok": True})
    except Exception as e:
        print(f"❌ Error al recrear sala: {e}")
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

    # Seleccionar letra inteligente (sin repetir y sin letras difíciles)
    letra = seleccionar_letra_aleatoria(sala)
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

    # Obtener información de equipos
    equipos = sala.get("equipos", {})
    equipos_data = {}
    if modo_juego == "equipos" and equipos:
        equipos_data = equipos

    return render_template("game.html",
                           jugador=sala["anfitrion"],
                           anfitrion=sala["anfitrion"],
                           codigo=codigo,
                           ronda=sala.get("ronda_actual", 1),
                           total_rondas=sala.get("rondas", 1),
                           letra=letra,
                           categorias=categorias_con_iconos,
                           powerups_habilitados=sala.get("powerups_habilitados", True),
                           chat_habilitado=sala.get("chat_habilitado", True),
                           validacion_activa=sala.get("validacion_activa", False),
                           modo_juego=modo_juego,
                           equipos=equipos_data)



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

@socketio.on("anfitrion_recrear_sala")
def handle_anfitrion_recrear_sala(data):
    """Manejar cuando el anfitrión quiere recrear la sala"""
    codigo_anterior = data.get("codigo_anterior")
    nombre_anfitrion = data.get("nombre")
    
    sala_anterior = state["salas"].get(codigo_anterior)
    if not sala_anterior:
        socketio.emit("error_recrear_sala", {"mensaje": "Sala anterior no encontrada"}, room=request.sid)
        return
    
    if nombre_anfitrion != sala_anterior.get("anfitrion"):
        socketio.emit("error_recrear_sala", {"mensaje": "No tienes permiso para recrear esta sala"}, room=request.sid)
        return
    
    # Verificar si ya existe una sala recreada
    codigo_nuevo = state.get("salas_recreadas", {}).get(codigo_anterior)
    if not codigo_nuevo:
        socketio.emit("error_recrear_sala", {"mensaje": "La nueva sala aún no ha sido creada"}, room=request.sid)
        return
    
    # Notificar a todos los jugadores de la sala anterior que hay una nueva sala
    socketio.emit("nueva_sala_disponible", {
        "codigo_nuevo": codigo_nuevo,
        "codigo_anterior": codigo_anterior,
        "anfitrion": nombre_anfitrion
    }, room=codigo_anterior)
    
    print(f"🔄 Anfitrión {nombre_anfitrion} recreó sala {codigo_anterior} → {codigo_nuevo}")

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
        
        # Limpiar lista de jugadores desconectados al iniciar nueva ronda
        # Solo mantener aquellos que realmente NO tienen una conexión activa
        jugadores_realmente_desconectados = []
        for jugador_desc in sala.get("jugadores_desconectados", []):
            # Verificar si el jugador tiene algún socket activo
            tiene_conexion_activa = False
            for sid_activo, nombre_activo in sid_to_name.items():
                if nombre_activo == jugador_desc and sid_to_room.get(sid_activo) == codigo:
                    tiene_conexion_activa = True
                    break
            
            # Si no tiene conexión activa, mantenerlo en la lista de desconectados
            if not tiene_conexion_activa:
                jugadores_realmente_desconectados.append(jugador_desc)
        
        sala["jugadores_desconectados"] = jugadores_realmente_desconectados
        
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
    """Permite a un jugador usar un power-up durante la partida"""
    codigo = data.get("codigo")
    jugador = data.get("jugador")
    powerup = data.get("powerup")
    categoria = data.get("categoria", None)  # Para pista_ia
    
    sala = state["salas"].get(codigo)
    if not sala or not sala.get("powerups_habilitados", True):
        emit("powerup_error", {"error": "Power-ups no habilitados"})
        return
    
    if powerup not in POWERUPS:
        emit("powerup_error", {"error": "Power-up no válido"})
        return
    
    # Verificar si el jugador tiene el power-up
    powerups_jugador = sala.get("powerups_jugadores", {}).get(jugador, {})
    
    if powerups_jugador.get(powerup, 0) <= 0:
        emit("powerup_error", {"error": f"No tienes {POWERUPS[powerup]['nombre']}"})
        return
    
    # ========== APLICAR EFECTOS DE POWER-UPS ==========
    
    # ⏰ TIEMPO EXTRA: +30 segundos
    if powerup == "tiempo_extra":
        if not sala.get("en_curso", False):
            emit("powerup_error", {"error": "Solo puedes usar esto durante la partida"})
            return
        
        # Consumir power-up
        powerups_jugador[powerup] -= 1
        sala["powerups_jugadores"][jugador] = powerups_jugador
        
        # Agregar tiempo
        tiempo_actual = sala.get("tiempo_restante", 0)
        sala["tiempo_restante"] = tiempo_actual + 30
        
        # Notificar a todos
        socketio.emit("update_timer", {"tiempo": sala["tiempo_restante"]}, room=codigo)
        socketio.emit("powerup_usado", {
            "jugador": jugador,
            "powerup": "tiempo_extra",
            "mensaje": f"⏰ {jugador} agregó +30 segundos!"
        }, room=codigo)
        
        print(f"⏰ {jugador} usó Tiempo Extra (+30s) - Tiempo total: {sala['tiempo_restante']}s")
    
    # 💡 PISTA IA: Sugerencia de palabra válida
    elif powerup == "pista_ia":
        if not OPENAI_AVAILABLE or not openai_client:
            emit("powerup_error", {"error": "IA no disponible"})
            return
        
        if not categoria:
            emit("powerup_error", {"error": "Debes especificar una categoría"})
            return
        
        letra = sala.get("letra", "A")
        
        # Consumir power-up
        powerups_jugador[powerup] -= 1
        sala["powerups_jugadores"][jugador] = powerups_jugador
        
        # Generar pista con IA
        try:
            prompt = f"""Dame una palabra válida en español para la categoría "{categoria}" que empiece con la letra "{letra}".

IMPORTANTE:
- Debe ser una palabra REAL que exista
- Debe ser apropiada y verificable
- Solo responde con la palabra, sin explicaciones

Ejemplo: Si categoría es "Animal" y letra "R", responde: Rinoceronte"""

            response = openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "Eres un asistente que sugiere palabras válidas para el juego BASTA/Stop."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.7,
                max_tokens=20,
                timeout=5
            )
            
            sugerencia = response.choices[0].message.content.strip()
            
            # Limpiar la respuesta (quitar puntos, comillas, etc.)
            sugerencia = sugerencia.replace('"', '').replace("'", '').replace('.', '').strip()
            
            # Enviar pista solo al jugador que la pidió
            emit("pista_recibida", {
                "categoria": categoria,
                "sugerencia": sugerencia,
                "letra": letra
            })
            
            print(f"💡 {jugador} usó Pista IA para '{categoria}' con letra '{letra}' → Sugerencia: {sugerencia}")
            
        except Exception as e:
            print(f"❌ Error generando pista IA: {e}")
            emit("powerup_error", {"error": "Error al generar pista"})
            # Devolver power-up si hubo error
            powerups_jugador[powerup] += 1
            sala["powerups_jugadores"][jugador] = powerups_jugador
            return
    
    # 💎 MULTIPLICADOR x2: Activar para próxima ronda
    elif powerup == "multiplicador":
        # Consumir power-up
        powerups_jugador[powerup] -= 1
        sala["powerups_jugadores"][jugador] = powerups_jugador
        
        # Activar multiplicador para próxima ronda
        if "powerups_activos" not in sala:
            sala["powerups_activos"] = {}
        if jugador not in sala["powerups_activos"]:
            sala["powerups_activos"][jugador] = []
        
        sala["powerups_activos"][jugador].append("multiplicador")
        
        # Notificar al jugador
        emit("multiplicador_activado", {
            "mensaje": "💎 Multiplicador x2 activado para la próxima ronda"
        })
        
        print(f"💎 {jugador} activó Multiplicador x2 para próxima ronda")
    
    save_state(state)
    
    # Enviar estado actualizado de power-ups al jugador
    emit("powerups_actualizados", {
        "powerups": sala["powerups_jugadores"][jugador]
    })



@socketio.on("dar_powerup")
def handle_dar_powerup(data):
    """Administrador puede dar power-ups a jugadores (solo para testing/recompensas especiales)"""
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
            "tiempo_extra": 0, "pista_ia": 0, "multiplicador": 0
        }
    
    sala["powerups_jugadores"][jugador_destino][powerup] = \
        sala["powerups_jugadores"][jugador_destino].get(powerup, 0) + 1
    
    save_state(state)
    
    socketio.emit("powerup_recibido", {
        "jugador": jugador_destino,
        "powerup": powerup,
        "cantidad": sala["powerups_jugadores"][jugador_destino][powerup],
        "nombre": POWERUPS[powerup]["nombre"]
    }, room=codigo)
    
    print(f"🎁 Admin {jugador_admin} dio {POWERUPS[powerup]['nombre']} a {jugador_destino}")


@socketio.on("solicitar_powerups")
def handle_solicitar_powerups(data):
    """Envía los power-ups actuales del jugador"""
    codigo = data.get("codigo")
    jugador = data.get("jugador")
    
    sala = state["salas"].get(codigo)
    if not sala:
        return
    
    powerups = sala.get("powerups_jugadores", {}).get(jugador, {
        "tiempo_extra": 0, 
        "pista_ia": 0, 
        "multiplicador": 0
    })
    
    emit("powerups_actualizados", {"powerups": powerups})


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