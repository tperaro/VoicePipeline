#!/usr/bin/env python3
"""Envia os videos prontos (videos_finais/*_IA.mp4) para a pasta do Google Drive (so stdlib; pode ir pro cron)."""

import argparse
import os
import sys

from studio import drive
from studio.config import ESTADO_PATH, RCLONE_REMOTE, REC_DIR, VIDEOS_DIR, load_estado, merge_estado
from studio.takes import Take


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Envia os vídeos prontos para a pasta do Google Drive (via rclone). "
                                            "Nunca apaga nada no Drive; o que já está igual é pulado.")
    p.add_argument("--pasta", metavar="LINK", help="link da pasta do Drive (fica salvo em estado.json)")
    p.add_argument("--arquivo", metavar="CAMINHO", action="append",
                   help="envia só este vídeo (pode repetir); sem ele, envia todos de videos_finais/ (respeitando "
                        "o histórico de envio); sempre envia, mesmo que já registrado")
    p.add_argument("--dry-run", action="store_true", help="só mostra o que seria enviado, sem enviar")
    p.add_argument("--reenviar", action="store_true",
                   help="ignora o histórico de envio (take.json) e reenvia tudo de videos_finais/, mesmo o que "
                        "já foi enviado antes")
    p.add_argument("--estado", default=ESTADO_PATH, help="caminho do estado.json (testes)")
    p.add_argument("--videos-dir", default=VIDEOS_DIR, help="pasta dos vídeos finais (testes)")
    p.add_argument("--rec-dir", default=REC_DIR, help="pasta das tomadas, para anotar o envio (testes)")
    return p


def _ja_enviado(path: str, rec_dir: str) -> bool:
    # decide so pelo historico local (take.json), nunca pelo que esta hoje na pasta do Drive: o dono da pasta
    # pode ter movido, renomeado ou apagado o arquivo la sem que isso deva gerar reenvio nem duplicata
    parts = drive.split_video_name(os.path.basename(path))
    if parts is None:
        return False
    take_id, model_key = parts
    try:
        take = Take.load(os.path.join(rec_dir, take_id))
    except (OSError, ValueError, TypeError):
        return False
    md5_registrado = ((take.saidas.get(model_key) or {}).get("enviado") or {}).get("md5")
    if not md5_registrado:
        return False
    try:
        return md5_registrado == drive.md5_file(path)
    except OSError:
        return False


class Progress:
    # uma linha a cada 10% por arquivo (legivel no terminal e no log do cron)
    def __init__(self):
        self.last: dict[str, int] = {}

    def __call__(self, name: str, frac: float) -> None:
        step = int(frac * 100) // 10 * 10
        if self.last.get(name) == step:
            return
        self.last[name] = step
        print(f"  {name}: {step}%", flush=True)


def describe(res: dict, dry_run: bool) -> str:
    if not res["ok"]:
        return "FALHOU — " + res["erro"].replace("\n", "\n    ")
    if res["pulado"]:
        return "já estava igual no Drive (pulado)"
    return "seria enviado" if dry_run else "enviado"


def run(args) -> int:
    estado, aviso = load_estado(args.estado)
    if aviso:
        print(aviso, file=sys.stderr)
    if args.pasta is not None:
        try:
            drive.parse_folder_link(args.pasta)
        except drive.DriveError as e:
            print(f"Link inválido: {e}", file=sys.stderr)
            return 2
        # grava so a pasta, relendo o arquivo; um estado.json corrompido fica guardado em .corrompido
        estado, aviso = merge_estado({"drive_pasta": args.pasta.strip()}, args.estado)
        if aviso:
            print(aviso, file=sys.stderr)
        print("Pasta do Drive salva em estado.json")
    link = str(estado.get("drive_pasta") or "").strip()
    if not link:
        print("Nenhuma pasta do Drive configurada — use --pasta <link> (veja o README)", file=sys.stderr)
        return 2
    try:
        drive.parse_folder_link(link)
    except drive.DriveError as e:
        print(f"Link da pasta salvo em estado.json é inválido: {e}", file=sys.stderr)
        return 2
    if args.arquivo:
        # --arquivo e explicito: sempre envia, mesmo que o take.json ja registre esse video como enviado
        paths, ja_enviados = [os.path.abspath(a) for a in args.arquivo], []
    else:
        candidatos = drive.list_videos(args.videos_dir)
        if args.reenviar:
            paths, ja_enviados = candidatos, []
        else:
            paths, ja_enviados = [], []
            for path in candidatos:
                (ja_enviados if _ja_enviado(path, args.rec_dir) else paths).append(path)
    if not paths and not ja_enviados:
        print(f"Nenhum vídeo para enviar em {args.videos_dir}")
        return 0
    if args.dry_run:
        print(f"Simulação (--dry-run): {len(paths)} vídeo(s), nada é enviado")
    else:
        print(f"Enviando {len(paths)} vídeo(s) para o Drive…")
    results = []
    if paths:
        try:
            results = drive.upload_files(paths, link, on_progress=Progress(), dry_run=args.dry_run,
                                         videos_dir=args.videos_dir)
        except drive.DriveError as e:
            print(f"Erro: {e}", file=sys.stderr)
            return 1
    for path in ja_enviados:
        print(f"{os.path.basename(path)}: pulado (já enviado antes)")
    for res in results:
        print(f"{os.path.basename(res['arquivo'])}: {describe(res, args.dry_run)}")
        if res["aviso"]:
            print(f"  aviso: {res['aviso']}")
        if res["erro"] == drive.MSG_RELOGIN:
            print(f"  no terminal: rclone config reconnect {RCLONE_REMOTE}:")
        if not args.dry_run:
            drive.record_sent(res, args.rec_dir)
    sent = sum(1 for r in results if r["ok"] and not r["pulado"])
    skipped = sum(1 for r in results if r["pulado"]) + len(ja_enviados)
    failed = len(paths) - sent - (skipped - len(ja_enviados))
    if args.dry_run:
        print(f"Resumo (simulação): {sent} a enviar, {skipped} pulado(s), {failed} falha(s)")
    else:
        print(f"Resumo: {sent} enviado(s), {skipped} pulado(s), {failed} falha(s)")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except KeyboardInterrupt:
        # o Ctrl-C tambem chega ao rclone (mesmo grupo de processos)
        print("\nEnvio cancelado", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
