from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

PUBLIC_COMPANION_README = r'''VÓRTICE PUBLIC BETA — CHATGPT HANDOFF
=====================================

A distribuição pública do Vórtice usa o fluxo Assistido para o ChatGPT Web.
O Vórtice prepara o CONTEXTO.zip, o prompt e os anexos, abre o ChatGPT e mantém
Task/Rodada/histórico localmente. O envio e o retorno do arquivo ficam sob ação
do usuário.

Esta build pública não instala uma extensão que leia ou baixe automaticamente
respostas do ChatGPT Web. Isso deixa o pacote mais simples, previsível e adequado
para distribuição pública.

Fluxo:
1. COZINHAR CONTEXTO
2. MANDAR PRO GPT
3. confira/envie o prompt + arquivos no ChatGPT
4. baixe o ZIP final
5. arraste/selecione o ZIP no Vórtice e aplique

O Vórtice continua validando identidade de Task/Rodada, criando backup e
mantendo o histórico de aplicação.
'''


def ensure_extension_files(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    # Public builds never leave a previously materialized automation extension
    # active by accident. Keep only the explanatory README in this directory.
    for child in directory.iterdir():
        if child.name == "README.txt":
            continue
        try:
            if child.is_dir():
                import shutil
                shutil.rmtree(child)
            else:
                child.unlink()
        except Exception:
            pass
    (directory / "README.txt").write_text(PUBLIC_COMPANION_README, encoding="utf-8")
    return directory.resolve()


class CompanionBridge:
    """Public-build stub.

    The public distribution intentionally does not automate extraction/download
    of ChatGPT Web output. The desktop app falls back to its assisted handoff.
    """

    def __init__(self, token: str, event_queue: Any, *, port: int = 8765):
        self.token = str(token or "")
        self.event_queue = event_queue
        self.port = int(port)

    def start(self) -> bool:
        return False

    def stop(self) -> None:
        return None

    def queue_handoff(self, payload: dict, files: Iterable[Path] | None = None) -> str:
        return ""
