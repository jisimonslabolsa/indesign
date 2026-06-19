# idml2banner

**IDML → HTML5 IAB Banner Generator**

Convierte un archivo master de InDesign (`.idml`) en banners HTML5 listos para subir a ad servers (DV360, Xandr, Adform, Sizmek).

---

## Pipeline

```
master.indd  ──[InDesign]──▶  master.idml
                                   │
                          [1] EXTRACTOR (Python)
                          Parsea spreads, stories,
                          estilos, colores, posiciones
                                   │
                            layout.json
                                   │
                          [2] SCALER (opcional)
                          Escala proporcional a
                          los tamaños IAB target
                                   │
                          [3] RENDERER
                          CSS absolute positioning
                          + clickTag + fallback.jpg
                                   │
                          banner_300x250.zip
                          banner_728x90.zip
                          banner_320x50.zip  ...
```

---

## Requisitos

```bash
pip install simpleidml lxml Pillow
```

Python 3.10+

---

## Uso

### Básico (mantiene tamaño del master)
```bash
python3 idml2banner.py mi_banner.idml
```

### Múltiples tamaños IAB
```bash
python3 idml2banner.py mi_banner.idml --sizes 300x250,728x90,320x50,160x600,300x600
```

### Con assets enlazados e URL de destino
```bash
python3 idml2banner.py mi_banner.idml \
  --sizes 300x250,728x90 \
  --assets ./links \
  --click https://www.tudominio.com/landing \
  --out ./banners_output
```

### Solo extraer JSON (sin renderizar)
```bash
python3 idml2banner.py mi_banner.idml --json-only
```

---

## Parámetros

| Parámetro    | Descripción                                          | Default               |
|--------------|------------------------------------------------------|-----------------------|
| `idml`       | Ruta al archivo `.idml`                              | (requerido)           |
| `--sizes`    | Tamaños IAB target separados por coma `WxH`          | Tamaño del master     |
| `--assets`   | Carpeta con imágenes enlazadas del InDesign           | —                     |
| `--click`    | URL de click-through                                 | `%%CLICK_URL_UNESC%%` |
| `--out`      | Directorio de salida                                 | `./output`            |
| `--json-only`| Solo extrae el JSON de layout                        | false                 |

---

## Preparar el IDML desde InDesign

1. **Archivo > Exportar > Adobe IDML (.idml)**
2. Si el banner tiene imágenes enlazadas:  
   **Archivo > Empaquetar** → copia las imágenes a la carpeta `links/`  
   Usa esa carpeta con `--assets ./links`

---

## Tamaños IAB soportados

| Tamaño    | Nombre                |
|-----------|-----------------------|
| 300×250   | Medium Rectangle      |
| 728×90    | Leaderboard           |
| 160×600   | Wide Skyscraper       |
| 300×600   | Half Page             |
| 320×50    | Mobile Banner         |
| 970×250   | Billboard             |
| 300×50    | Mobile Banner Small   |
| 320×100   | Large Mobile Banner   |

Cualquier tamaño custom también funciona.

---

## Output por banner

Cada banner genera un `.zip` con:

```
banner_300x250.zip
├── index.html      ← Banner HTML5 con clickTag IAB
├── fallback.jpg    ← Imagen estática de fallback
└── manifest.json   ← Metadatos del banner
```

---

## Estructura del proyecto

```
idml2banner/
├── idml2banner.py              ← CLI principal
├── extractor/
│   └── idml_parser.py          ← IDML → JSON
├── renderer/
│   └── html5_renderer.py       ← JSON → HTML5 zip
├── samples/
│   └── sample_banner.idml      ← IDML de prueba (300×250)
└── output/                     ← Salida por defecto
```

---

## Limitaciones conocidas (v0.1)

| Limitación | Estado |
|---|---|
| Tipografía exacta (kerning/tracking) | Aproximado con CSS letter-spacing |
| Efectos InDesign (sombras, degradados) | No implementado |
| Imágenes embebidas en IDML | No implementado (solo enlazadas) |
| Fuentes personalizadas | Requiere incluir font files manualmente |
| Animaciones CSS automáticas | Solo fade-in básico |

---

## Roadmap

- [ ] Validador visual con Puppeteer (diff screenshot vs PDF export)  
- [ ] Soporte de gradients (exportar como imagen)  
- [ ] Animaciones CSS desde capas InDesign con timings  
- [ ] Web UI (drag & drop IDML → descarga zips)  
- [ ] Soporte de fuentes web (Google Fonts auto-match)

---

## Licencia

MIT
