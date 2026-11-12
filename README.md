# AI Skills

Las skills que uso a diario con Claude, cada una también en versión para ChatGPT. Cubren datos y Power BI, desarrollo, seguridad, diseño, escritura y negocio, sin duplicados entre sí. La lista completa con una línea por skill está en [CATALOGO.md](CATALOGO.md).

Muchas parten de trabajo de otras personas que adapté a mi forma de trabajar; el crédito y la licencia de cada una están en [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Cómo está armado

```
skills/<skill>/        versión Claude. Es la fuente de verdad.
chatgpt/<skill>/       versión ChatGPT, generada desde skills/ con el script.
catalog.json           categoría, nombre visible y descripción corta de cada skill.
CATALOGO.md            se genera desde catalog.json.
scripts/               build_chatgpt.py y package.py.
.claude-plugin/        para instalar todo como plugin en Claude Code.
```

## Instalar

Empaquetar primero: `python3 scripts/package.py` deja un zip por skill en `dist/claude/` y `dist/chatgpt/`. Para una sola: `python3 scripts/package.py <skill>`.

- Claude (app o web): subir el zip de `dist/claude/` en la sección de Skills de Configuración.
- Claude Code, todo de una vez: `/plugin marketplace add ricgiorgis/AI_Skills` y luego `/plugin install ai-skills@ai-skills`.
- Claude Code, una sola: copiar `skills/<skill>` a `~/.claude/skills/`.
- ChatGPT: subir el zip de `dist/chatgpt/` en Skills.
- Codex: copiar `chatgpt/<skill>` al directorio de skills de Codex.

## Modificar o agregar una skill

1. Cambiá lo que haga falta en `skills/<skill>/`.
2. Si es nueva, agregala en `catalog.json` con su categoría, nombre visible y descripción corta.
3. Corré `python3 scripts/build_chatgpt.py <skill>` (o sin argumentos para regenerar todo). Eso actualiza `chatgpt/<skill>/` y `CATALOGO.md`.

Qué cambia el script en la versión ChatGPT: deja solo `name` y `description` en el frontmatter, cambia rutas como `~/.claude/skills/<skill>/` por `<SKILL_DIR>/`, agrega al inicio una nota de plataforma que explica qué hacer con las herramientas de Claude que menciona el texto (AskUserQuestion, SendUserFile, WebFetch, etc.) y escribe `agents/openai.yaml`. Los scripts de código se copian tal cual.

## Power BI y Fabric: lo que no está aquí

Para Power BI uso además dos colecciones que no se redistribuyen en este repo porque sus licencias no lo permiten. Se instalan directo desde su origen:

- [data-goblin/power-bi-agentic-development](https://github.com/data-goblin/power-bi-agentic-development) de Kurt Buhler: TMDL, PBIR, PBIP, Tabular Editor, BPA, Fabric CLI, Power BI Desktop, visuales custom, paginados, auditoría de tenant y más. En Claude Code: `claude plugin marketplace add data-goblin/power-bi-agentic-development`.
- [lukasreese/powerbi-claude-skills](https://github.com/lukasreese/powerbi-claude-skills): levantamiento de requerimientos y construcción de reportes PBIR con visuales IBCS.

Las skills de Power BI de este repo (measures, calc-groups, dax-udf, tmdl-standards, standardize-naming-conventions, iconography) funcionan solas, pero rinden más junto con esas dos.

## Otras que no están

- docx, pdf, pptx, xlsx: son de Anthropic con licencia propietaria y ya vienen integradas en Claude.
- duelo-a-muerte: tiene repo propio, [duelo-con-cuchillos](https://github.com/ricgiorgis/duelo-con-cuchillos).

## Licencia

MIT para el repo y mis skills (ver [LICENSE](LICENSE)). Las de terceros conservan la suya, detallada en [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
