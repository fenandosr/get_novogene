"""
fix.py — utilidades para corregir datos de runs guardados.

Uso:
  python fix.py fix-cancelled --samples MX001 MX002 [--run FILE] [--dry-run]
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

STAGE_ORDER = [
    "PENDING_ARRIVAL",
    "RECEIVED",
    "SAMPLE_QC",
    "LIB_PREP_OR_SEQUENCING",
    "DATA_QC",
    "FINAL_REPORT",
    "DATA_RELEASE",
]

LOGS_DIR = Path(__file__).parent / "logs"


def _emit(msg: str, log_fh) -> None:
    print(msg)
    log_fh.write(msg + "\n")


def fix_samples_cancelled(
    sample_names: list[str],
    run_file: str | None = None,
    dry_run: bool = False,
) -> list[dict]:
    """Asigna FINAL_REPORT = fecha del último stage definido para cada muestra indicada.

    Args:
        sample_names: lista de sampleName a corregir.
        run_file:     nombre de archivo dentro de runs/ (default: el más reciente).
        dry_run:      si True, muestra los cambios sin escribir nada.

    Returns:
        Lista de registros con el resultado por muestra.
    """
    from storage import list_runs

    # Resolver ruta del run
    if run_file:
        run_path = Path(__file__).parent / "runs" / run_file
        if not run_path.exists():
            raise FileNotFoundError(f"Run no encontrado: {run_path}")
    else:
        runs = list_runs()
        if not runs:
            raise RuntimeError("No hay runs. Ejecuta el scraper primero.")
        run_path = runs[0]

    run = json.loads(run_path.read_text())

    target = set(sample_names)
    found = set()
    changes = []

    for project in run["projects"]:
        for sample in project["samples"]:
            name = sample["sample_name"]
            if name not in target:
                continue
            found.add(name)

            tl = sample["timeline"]

            if tl.get("FINAL_REPORT"):
                changes.append({
                    "sample": name,
                    "project": project["sub_project_no"],
                    "status": "omitida",
                    "detalle": f"FINAL_REPORT ya existe ({tl['FINAL_REPORT'][:10]})",
                })
                continue

            # Último stage con fecha definida (según el orden del pipeline)
            last_stage, last_date = None, None
            for stage in STAGE_ORDER:
                if tl.get(stage):
                    last_stage, last_date = stage, tl[stage]

            if last_date is None:
                changes.append({
                    "sample": name,
                    "project": project["sub_project_no"],
                    "status": "omitida",
                    "detalle": "ningún stage tiene fecha",
                })
                continue

            changes.append({
                "sample": name,
                "project": project["sub_project_no"],
                "status": "dry-run" if dry_run else "corregida",
                "ultimo_stage": last_stage,
                "final_report_date": last_date[:10],
            })

            if not dry_run:
                sample["timeline"]["FINAL_REPORT"] = last_date

    # Muestras no encontradas
    for name in target - found:
        changes.append({
            "sample": name,
            "project": None,
            "status": "no_encontrada",
            "detalle": "no existe en este run",
        })

    # Persistir cambios
    if not dry_run:
        run_path.write_text(json.dumps(run, indent=2, ensure_ascii=False))

    # Logging
    LOGS_DIR.mkdir(exist_ok=True)
    ts = datetime.now(ZoneInfo("America/Mexico_City")).strftime("%Y%m%d-%H%M%S")
    log_path = LOGS_DIR / f"fix-cancelled-{ts}.log"

    with log_path.open("w") as lf:
        _emit(f"{'[DRY-RUN] ' if dry_run else ''}fix-cancelled — {datetime.now(ZoneInfo('America/Mexico_City')).strftime('%Y-%m-%d %H:%M CDMX')}", lf)
        _emit(f"Run: {run_path.name}", lf)
        _emit(f"Muestras solicitadas: {len(sample_names)}  |  Encontradas: {len(found)}", lf)
        _emit("-" * 60, lf)

        corregidas = [c for c in changes if c["status"] in ("corregida", "dry-run")]
        omitidas   = [c for c in changes if c["status"] == "omitida"]
        no_enc     = [c for c in changes if c["status"] == "no_encontrada"]

        if corregidas:
            _emit(f"\n✔ {'Serían corregidas' if dry_run else 'Corregidas'} ({len(corregidas)}):", lf)
            for c in corregidas:
                _emit(f"  {c['sample']}  [{c['project']}]  {c['ultimo_stage']} → FINAL_REPORT = {c['final_report_date']}", lf)

        if omitidas:
            _emit(f"\n⚠ Omitidas ({len(omitidas)}):", lf)
            for c in omitidas:
                _emit(f"  {c['sample']}  [{c['project']}]  — {c['detalle']}", lf)

        if no_enc:
            _emit(f"\n✗ No encontradas ({len(no_enc)}):", lf)
            for c in no_enc:
                _emit(f"  {c['sample']}", lf)

        _emit(f"\nLog guardado en: {log_path}", lf)

    return changes


def _cmd_fix_cancelled(args) -> None:
    changes = fix_samples_cancelled(
        sample_names=args.samples,
        run_file=args.run,
        dry_run=args.dry_run,
    )
    ok = sum(1 for c in changes if c["status"] == "corregida")
    sys.exit(0 if ok or args.dry_run else 1)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="fix",
        description="Herramientas para corregir datos de runs de Novogene.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser(
        "fix-cancelled",
        help="Asigna FINAL_REPORT a muestras canceladas/atascadas.",
    )
    p.add_argument("--samples", nargs="+", required=True, metavar="NAME",
                   help="Nombres de muestras a corregir (sampleName)")
    p.add_argument("--run", metavar="FILE",
                   help="Archivo de run en runs/ (default: el más reciente)")
    p.add_argument("--dry-run", action="store_true",
                   help="Muestra los cambios sin escribirlos")
    p.set_defaults(func=_cmd_fix_cancelled)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
