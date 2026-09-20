# Privacidade

O Vórtice 4.4.2 Public Beta é local-first e não implementa telemetria própria.

## O que fica local

Por padrão, o estado do usuário é armazenado em `%LOCALAPPDATA%\Vortice`, incluindo projetos cadastrados, Tasks, rodadas, prompts, histórico, checklist, skills, memória local, ZIPs preparados e backups de código.

## O que pode sair do computador

Somente o que o próprio usuário decide enviar a serviços externos. No fluxo com ChatGPT, o Vórtice prepara os arquivos e o prompt; a edição pública usa handoff assistido e o usuário confirma o envio.

## Segredos

O coletor possui filtros para padrões comuns de segredo (`.env`, chaves privadas, credenciais e formatos semelhantes), mas nenhum filtro é perfeito. Revise contextos sensíveis antes de enviá-los para qualquer serviço externo.

## Serviços externos

Git, VS Code, Codex CLI, ChatGPT e outros programas/serviços possuem seus próprios termos e políticas. O Vórtice não controla esses serviços.
