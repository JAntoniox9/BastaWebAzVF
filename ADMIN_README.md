# 🔐 Panel de Super Administración - Basta Web

## Acceso al Panel Admin

**URL:** `http://127.0.0.1:8081/admin`

**Contraseña por defecto:** `basta2024`

> ⚠️ **IMPORTANTE**: Cambia la contraseña en `app.py` línea 1125:
> ```python
> ADMIN_PASSWORD = "tu_nueva_contraseña_segura"
> ```

---

## Características del Panel

### 📊 Dashboard Principal

**Estadísticas en Tiempo Real:**
- 🏠 **Total de Salas** - Número de salas creadas
- 🎮 **Salas Activas** - Partidas en juego actualmente  
- 👥 **Jugadores Online** - Total de jugadores conectados
- 💬 **Mensajes Chat** - Total de mensajes enviados

### 🏠 Monitor de Salas

**Información por Sala:**
- Código de la sala
- Nombre del anfitrión
- Número de jugadores
- Modo de juego (Clásico, Rápido, Equipos, Duelo)
- Ronda actual / Total
- Cantidad de mensajes
- Estado (En juego / Esperando)

**Acción:** Click en cualquier sala para ver su chat

### 💬 Monitor de Chat

**Características:**
- Ver todos los mensajes de cualquier sala
- Nombre del jugador que escribió
- Hora del mensaje
- Actualización automática cada 5 segundos
- Scroll automático a mensajes nuevos

### 🔄 Actualizaciones Automáticas

El panel se actualiza automáticamente cada 5 segundos para mostrar:
- Nuevas salas creadas
- Cambios en el estado de las salas
- Nuevos mensajes de chat
- Jugadores que se unen/salen
- Estadísticas actualizadas

---

## Diferencias: Admin Global vs Admin de Sala

### 👑 Admin de Sala (Anfitrión)
- **Acceso:** Automático al crear una sala
- **Alcance:** Solo su sala
- **Funciones:**
  - Activar/desactivar funcionalidades de SU sala
  - Iniciar partidas
  - Dar power-ups
  - Aplicar penalizaciones

### 🔐 Super Admin (Tú)
- **Acceso:** Con contraseña en `/admin`
- **Alcance:** TODO el sistema
- **Funciones:**
  - Ver TODAS las salas
  - Monitorear TODOS los chats
  - Estadísticas globales
  - Supervisión completa

**Ambos pueden coexistir sin problemas**

---

## Cómo Usar

### 1. Acceder al Panel
```
http://127.0.0.1:8081/admin
```

### 2. Iniciar Sesión
- Ingresa la contraseña: `basta2024`
- Click en "Iniciar Sesión"

### 3. Monitorear Salas
- Verás todas las salas en el panel izquierdo
- Las salas activas (en juego) tienen borde verde
- Las salas en espera tienen borde naranja

### 4. Ver Chat de una Sala
- Click en cualquier sala
- El panel derecho mostrará todos los mensajes
- Se actualizará automáticamente

### 5. Cerrar Sesión
- Click en "🚪 Salir" en la esquina superior derecha
- O visita: `http://127.0.0.1:8081/admin/logout`

---

## API Endpoints (Solo Admin)

### GET `/api/admin/salas`
Obtener lista de todas las salas activas

**Requiere:** Cookie `admin_auth` con contraseña correcta

**Respuesta:**
```json
{
  "ok": true,
  "salas": [
    {
      "codigo": "ABC12",
      "anfitrion": "Juan",
      "jugadores": ["Juan", "María"],
      "estado": "espera",
      "ronda_actual": 1,
      "total_rondas": 3,
      "modo_juego": "clasico",
      "en_curso": false,
      "num_mensajes": 5
    }
  ],
  "total_salas": 1
}
```

### GET `/api/admin/sala/<codigo>/chat`
Obtener mensajes de chat de una sala específica

**Requiere:** Cookie `admin_auth`

**Respuesta:**
```json
{
  "ok": true,
  "codigo": "ABC12",
  "mensajes": [
    {
      "jugador": "Juan",
      "mensaje": "Hola!",
      "timestamp": "2024-01-01T12:00:00"
    }
  ],
  "anfitrion": "Juan"
}
```

### GET `/api/admin/estadisticas`
Obtener estadísticas del sistema

**Requiere:** Cookie `admin_auth`

**Respuesta:**
```json
{
  "ok": true,
  "estadisticas": {
    "total_salas": 5,
    "salas_activas": 2,
    "salas_en_espera": 3,
    "total_jugadores": 12,
    "total_mensajes": 45
  }
}
```

---

## Seguridad

### Cambiar la Contraseña

1. Abre `app.py`
2. Busca la línea 1125:
   ```python
   ADMIN_PASSWORD = "basta2024"
   ```
3. Cambia por una contraseña segura:
   ```python
   ADMIN_PASSWORD = "mi_contraseña_super_segura_123!"
   ```
4. Reinicia el servidor

### Recomendaciones

- ✅ **Cambia la contraseña** inmediatamente
- ✅ **No compartas** la contraseña del panel admin
- ✅ **Usa HTTPS** en producción
- ✅ **Implementa autenticación más robusta** para producción (JWT, OAuth, etc.)
- ✅ La sesión dura **24 horas** antes de pedir contraseña nuevamente

---

## Acceso Rápido

Desde la página principal (`http://127.0.0.1:8081`):
- Scroll hasta abajo
- Click en "🔐 Panel Admin" (enlace discreto en el footer)

---

## Solución de Problemas

### "Contraseña incorrecta"
- Verifica que estás usando: `basta2024`
- Si cambiaste la contraseña, usa la nueva

### "No se muestran salas"
- Verifica que hay salas creadas
- Refresca la página (F5)
- Click en "🔄 Actualizar"

### "Chat vacío"
- Selecciona una sala del panel izquierdo
- Asegúrate que la sala tenga mensajes
- El chat puede estar vacío si nadie ha escrito

### "Panel no carga"
- Verifica que el servidor esté corriendo
- Abre la consola del navegador (F12) para ver errores
- Prueba cerrar sesión y volver a entrar

---

## Notas Técnicas

- El panel usa **Socket.IO** para actualizaciones en tiempo real
- Las cookies se almacenan por **24 horas**
- La actualización automática ocurre cada **5 segundos**
- Los datos se guardan en `game_state.json`

---

¡Listo para administrar tu sistema de Basta Web! 🎮✨

