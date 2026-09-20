# Vórtice 4.4.1 · Public Beta 🌀

Esta é uma rodada de confiabilidade para instalação em máquinas novas.

### O que mudou

- detecção do Codex no Windows ficou bem mais robusta;
- reconhecimento do instalador standalone oficial em `%LOCALAPPDATA%\Programs\OpenAI\Codex\bin`;
- reconhecimento do `CODEX_HOME`, runtime local do Codex/ChatGPT e shims npm comuns;
- caminho manual para `codex.exe` como fallback;
- tutorial inicial agora instala/repara o Codex, abre o login com ChatGPT e oferece `codex doctor`;
- Config ganhou a aba **Dependências**;
- o status vermelho do Codex no topo agora é clicável e abre o assistente;
- a tela de Automação pública deixou de mostrar uma opção que não podia ser alterada;
- mensagens de erro apontam diretamente para o assistente de configuração.

### Baixe assim

- **Vortice-Setup.exe** — recomendado.
- **Vortice-Portable.zip** — portátil.
- **SHA256SUMS.txt** — hashes dos arquivos.

> O Windows pode mostrar SmartScreen enquanto o executável ainda não possui assinatura Authenticode/reputação estabelecida. Não é necessário desativar o Windows Defender.

O Vórtice é independente e não é afiliado, patrocinado ou endossado pela OpenAI.
