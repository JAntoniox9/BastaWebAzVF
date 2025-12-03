/**
 * Sistema para ocultar rutas en la URL
 * Se ejecuta DESPUÉS de que la página cargue completamente
 * para no interferir con la navegación
 

(function() {
    'use strict';
    
    function ocultarRuta() {
        if (window.location.pathname !== '/') {
            try {
                // Guardar la ruta real en sessionStorage por si se necesita
                sessionStorage.setItem('_realPath', window.location.pathname);
                window.history.replaceState({ hidden: true }, '', '/');
            } catch(e) {
                console.warn('No se pudo ocultar la ruta:', e);
            }
        }
    }
    
    // Solo ejecutar cuando el DOM esté completamente listo
    if (document.readyState === 'complete') {
        // Si ya está completo, esperar un poco más para asegurar
        setTimeout(ocultarRuta, 100);
    } else {
        // Esperar al evento load (después de todos los recursos)
        window.addEventListener('load', function() {
            setTimeout(ocultarRuta, 100);
        });
    }
    
    // Manejar navegación hacia atrás
    window.addEventListener('popstate', function(e) {
        if (e.state && e.state.hidden) {
            // Si es un estado oculto, mantenerlo
            return;
        }
        // Ocultar después de un pequeño delay
        setTimeout(ocultarRuta, 50);
    });
    
})();
*/