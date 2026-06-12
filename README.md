# check_novogene

Scraper y dashboard para monitorear el turnaround time (TAT) y el progreso de muestras WES en [Novogene CSS America](https://cssamerica.novogene.com).

---

## Instalación

Requiere Python 3.12+ y [uv](https://docs.astral.sh/uv/).

```bash
git clone <repo>
cd check_novogene
uv sync
cp .env.example .env
```

Edita `.env` con tus credenciales (ver sección de configuración).

---

## Configuración

### `.env`

| Variable | Descripción | Default |
|---|---|---|
| `NOVOGENE_USERNAME` | Email de tu cuenta en Novogene | — |
| `NOVOGENE_PASSWORD` | Contraseña | — |
| `NOVOGENE_PROJECTS` | Números de sub-proyecto separados por coma | — |
| `NOVOGENE_EXPECTED_TAT_DAYS` | Umbral de días para marcar muestras como retrasadas | `30` |
| `SERVER_PORT` | Puerto del servidor web | `8080` |
| `NOVOGENE_TOKEN` | Token manual de respaldo (opcional, ver abajo) | — |

Ejemplo mínimo:

```
NOVOGENE_USERNAME=usuario@empresa.com
NOVOGENE_PASSWORD=mipassword
NOVOGENE_PROJECTS=X202SC25109710-Z01,X202SC25020451-Z01
NOVOGENE_EXPECTED_TAT_DAYS=30
SERVER_PORT=8080
```

### `projects.json` — aliases de proyectos

Asigna nombres cortos a los sub-proyectos para que aparezcan en el dashboard:

```json
{
  "X202SC25109710-Z01": "BRCA",
  "X202SC25020451-Z01": "Exoma"
}
```

---

## Uso

```bash
# Correr el scraper una vez y guardar los resultados
uv run python main.py scrape

# Iniciar el dashboard web (usa el último run guardado)
uv run python main.py serve

# Scrape + dashboard en un solo comando
uv run python main.py scrape serve
```

El dashboard queda disponible en `http://localhost:8080` (o el puerto en `SERVER_PORT`).

---

## Cómo funciona el login

Novogene CSS America usa un SSO ([HZero](https://open.hand-china.com/)) repartido en dos dominios:

- **portal-global.novogene.com** — portal de autenticación central
- **ocssamerica.novogene.com** — API REST de datos de muestras

El scraper automatiza el flujo OAuth completo en cada ejecución:

1. **Login al portal** — `POST portal-global.novogene.com/login` con `username=GLOBAL_{email}` y la contraseña. El portal devuelve una sesión autenticada (cookie `HSKP_TOKEN`).

2. **Obtener código OAuth** — `GET portal-global.novogene.com/oauth2/authorize` con `client_id=Qba3WPly`. El portal, al reconocer la sesión activa, redirige automáticamente con un authorization code.

3. **Intercambio SSO** — El código se procesa en `ocssamerica.novogene.com/oauth/sso/paas`, que ejecuta un intercambio server-to-server con el portal y emite un segundo redirect a `/oauth/oauth/authorize`.

4. **Token final** — La URL de redirección final contiene `#access_token=<UUID>`. Ese bearer token se usa en todos los requests a la API de muestras.

Como el token se obtiene de nuevo en cada run del scraper, **nunca es necesario actualizarlo manualmente**.

### Fallback manual

Si el login automático falla (por ejemplo, ante un cambio en la plataforma de Novogene), puedes copiar un token directamente desde el navegador y usarlo como respaldo:

1. Abre `cssamerica.novogene.com` e inicia sesión
2. DevTools → Network → cualquier request a `ocssamerica.novogene.com` → header `authorization:`
3. Copia el valor después de `bearer ` y ponlo en `.env`:

```
NOVOGENE_TOKEN=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```

El scraper usará este token si el login automático falla, y lo ignorará si el login funciona.

---

## Qué muestra el dashboard

### Resumen general

Tarjetas en la parte superior con métricas agregadas de todos los proyectos:

| Métrica | Descripción |
|---|---|
| **Total** | Número total de muestras |
| **Completas** | Muestras con Data Release |
| **En proceso** | Muestras sin Data Release |
| **Sin release** | Muestras que completaron análisis pero no tienen Data Release |
| **Retrasadas** | Muestras con más días que `NOVOGENE_EXPECTED_TAT_DAYS` sin completar |
| **TAT promedio** | Días promedio de Recepción → Data Release (solo muestras completas) |

### Cuellos de botella

Gráfica de barras horizontales con el tiempo promedio (días) entre cada etapa consecutiva del pipeline, ordenadas de mayor a menor. Identifica en qué transición se acumula más tiempo.

Las etapas del pipeline son:

```
Pending Arrival → Received → Sample QC → Lib Prep / Sequencing → Data QC → Final Report → Data Release
```

### Pendientes de Data Release

Tabla con todas las muestras que ya pasaron por alguna etapa pero todavía no tienen Data Release, mostrando cuántos días llevan esperando y un indicador de color (verde / amarillo / rojo según el umbral configurado).

### Tabla por proyecto

Por cada sub-proyecto, una tabla con todas sus muestras que incluye:

- **Muestra** — nombre interno y ID de Novogene
- **Producto** — tipo de servicio (p. ej. WES)
- **Progreso** — puntos de colores que representan las 7 etapas; el punto activo se resalta
- **TAT** — días de Recepción → Data Release; verde si está dentro del umbral, amarillo si excede hasta 10 días, rojo si excede más
- **Timeline detallado** (expandible) — fecha exacta de cada etapa y días entre etapas consecutivas
