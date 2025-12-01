/**
 * Sistema de Sonidos para Basta Web
 * Utiliza la Web Audio API para generar sonidos sintéticos
 */

class SoundSystem {
    constructor() {
        this.enabled = true;
        this.audioContext = null;
        this.masterVolume = 0.3;
        this.init();
    }

    init() {
        try {
            // Inicializar AudioContext solo cuando sea necesario
            if (typeof AudioContext !== 'undefined') {
                this.audioContext = new (window.AudioContext || window.webkitAudioContext)();
            }
        } catch (e) {
            console.warn('Web Audio API no soportada:', e);
            this.enabled = false;
        }
    }

    resume() {
        if (this.audioContext && this.audioContext.state === 'suspended') {
            this.audioContext.resume();
        }
    }

    setEnabled(enabled) {
        this.enabled = enabled;
    }

    playTone(frequency, duration, type = 'sine', volume = 1.0) {
        if (!this.enabled || !this.audioContext) return;

        this.resume();

        const oscillator = this.audioContext.createOscillator();
        const gainNode = this.audioContext.createGain();

        oscillator.connect(gainNode);
        gainNode.connect(this.audioContext.destination);

        oscillator.frequency.value = frequency;
        oscillator.type = type;

        const finalVolume = this.masterVolume * volume;
        gainNode.gain.setValueAtTime(finalVolume, this.audioContext.currentTime);
        gainNode.gain.exponentialRampToValueAtTime(0.01, this.audioContext.currentTime + duration);

        oscillator.start(this.audioContext.currentTime);
        oscillator.stop(this.audioContext.currentTime + duration);
    }

    // Sonidos específicos del juego
    playStart() {
        // Sonido ascendente para inicio
        this.playTone(440, 0.1, 'sine', 0.8);
        setTimeout(() => this.playTone(554, 0.1, 'sine', 0.8), 100);
        setTimeout(() => this.playTone(659, 0.15, 'sine', 1.0), 200);
    }

    playBasta() {
        // Sonido dramático para BASTA
        this.playTone(880, 0.3, 'square', 1.0);
        setTimeout(() => this.playTone(440, 0.4, 'sawtooth', 0.9), 150);
    }

    playJoin() {
        // Sonido suave para jugador entrando
        this.playTone(523, 0.1, 'sine', 0.6);
        setTimeout(() => this.playTone(659, 0.1, 'sine', 0.6), 80);
    }

    playReady() {
        // Click confirmación
        this.playTone(800, 0.05, 'sine', 0.7);
    }

    playTick() {
        // Tick del reloj
        this.playTone(1000, 0.03, 'sine', 0.4);
    }

    playWarning() {
        // Advertencia (tiempo bajo)
        this.playTone(400, 0.1, 'square', 0.8);
        setTimeout(() => this.playTone(400, 0.1, 'square', 0.8), 150);
    }

    playPowerup() {
        // Sonido mágico para power-up
        this.playTone(523, 0.08, 'sine', 0.7);
        setTimeout(() => this.playTone(659, 0.08, 'sine', 0.7), 80);
        setTimeout(() => this.playTone(784, 0.12, 'sine', 0.9), 160);
    }

    playError() {
        // Sonido de error
        this.playTone(200, 0.2, 'sawtooth', 0.6);
    }

    playSuccess() {
        // Sonido de éxito
        this.playTone(523, 0.1, 'sine', 0.7);
        setTimeout(() => this.playTone(659, 0.1, 'sine', 0.7), 100);
        setTimeout(() => this.playTone(784, 0.15, 'sine', 0.9), 200);
    }

    playVictory() {
        // Fanfarria de victoria
        const notes = [523, 523, 523, 659, 523, 784, 659];
        const durations = [0.15, 0.15, 0.15, 0.3, 0.2, 0.4, 0.5];
        let delay = 0;

        notes.forEach((note, i) => {
            setTimeout(() => {
                this.playTone(note, durations[i], 'sine', 0.8);
            }, delay);
            delay += durations[i] * 800;
        });
    }

    playMessage() {
        // Notificación de mensaje
        this.playTone(600, 0.05, 'sine', 0.5);
        setTimeout(() => this.playTone(700, 0.05, 'sine', 0.5), 50);
    }

    playButton() {
        // Click de botón
        this.playTone(1200, 0.05, 'sine', 0.5);
    }
}

// Instancia global
const soundSystem = new SoundSystem();

// Inicializar al hacer click (requerido por navegadores)
document.addEventListener('click', () => {
    soundSystem.resume();
}, { once: true });

