import logging
import sys

# Definición de códigos ANSI para colores
class ColoresANSI:
    """Clase para códigos de color ANSI."""
    GRIS = "\x1b[38;20m"
    VERDE = "\x1b[32;20m"
    AMARILLO = "\x1b[33;20m"
    ROJO = "\x1b[31;20m"
    ROJO_CLARO = "\x1b[31;1m"
    RESET = "\x1b[0m"

class ColorFormatter(logging.Formatter):
    """Formateador personalizado para logs con colores."""

    # Definimos el formato base. Incluye %(asctime)s para la hora.
    # Usamos %(levelname)-8s para alinear el nivel de log.
    FORMATO_BASE = "%(asctime)s - %(name)s - %(levelname)-8s - %(message)s"
    
    # Mapeo de niveles de log a sus respectivos formatos de color
    FORMATOS_COLOR = {
        logging.DEBUG: ColoresANSI.GRIS + FORMATO_BASE + ColoresANSI.RESET,
        logging.INFO: ColoresANSI.VERDE + FORMATO_BASE + ColoresANSI.RESET,
        logging.WARNING: ColoresANSI.AMARILLO + FORMATO_BASE + ColoresANSI.RESET,
        logging.ERROR: ColoresANSI.ROJO + FORMATO_BASE + ColoresANSI.RESET,
        logging.CRITICAL: ColoresANSI.ROJO_CLARO + FORMATO_BASE + ColoresANSI.RESET,
    }

    def format(self, record):
        """Asigna el formato de color según el nivel de log."""
        # Se obtiene el formato específico para el nivel del registro
        log_fmt = self.FORMATOS_COLOR.get(record.levelno)
        
        # Se crea un nuevo Formatter con el formato específico (incluyendo colores)
        formatter = logging.Formatter(log_fmt, datefmt="%Y-%m-%d %H:%M:%S")
        
        # Se retorna el registro formateado
        return formatter.format(record)
    
def setup_logging(level=logging.INFO):
    """
    Configura el sistema de logging para el proyecto.
    
    Esta configuración se aplica de forma global una vez.
    """
    
    # 1. Obtener el logger principal (puede ser el 'root' o uno con un nombre específico)
    logger = logging.getLogger("") # Si usas '', configuras el logger raíz
    logger.setLevel(level)         # Establece el nivel mínimo de log a manejar

    # Evita que se dupliquen los logs si hay una configuración básica previa
    if logger.hasHandlers():
        logger.handlers.clear()
        
    # 2. Crear un StreamHandler para enviar los logs a la consola (sys.stdout)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level) # Nivel mínimo para el handler

    # 3. Crear y aplicar el Formateador de Colores
    color_formatter = ColorFormatter()
    console_handler.setFormatter(color_formatter)

    # 4. Añadir el Handler al Logger
    logger.addHandler(console_handler)

    try:
        print("✅ Configuración de logging con colores y hora aplicada globalmente.")
    except UnicodeEncodeError:
        print("[OK] Configuracion de logging con colores y hora aplicada globalmente.")
