# Vórtice 4.4.2 · Public Beta 🌀

Esta rodada corrige a configuração do Codex no Windows para funcionar tanto em máquinas que **já possuem Codex** quanto em instalações novas.

### O que mudou

- o Vórtice não confia mais apenas no `PATH`: ele procura e valida instalações standalone, npm, CODEX_HOME, WinGet/Scoop e caminho manual;
- candidatos antigos/quebrados não bloqueiam uma instalação válida encontrada depois;
- caminhos copiados com aspas são normalizados antes da execução;
- o instalador automático agora **sempre valida o `codex.exe` real**, mesmo quando `install.ps1` termina com erro na própria etapa final de verificação;
- o diretório standalone documentado no Windows é usado explicitamente: `%LOCALAPPDATA%\Programs\OpenAI\Codex\bin`;
- se o standalone realmente falhar e npm já existir na máquina, o Vórtice tenta `@openai/codex` como fallback sem instalar Node por conta própria;
- `Localizar codex.exe` só salva o arquivo depois de executar `codex --version` com sucesso;
- novo botão **Copiar diagnóstico** no assistente do Codex;
- `INSTALL-CODEX.bat` recebeu a mesma regra de recuperação: um erro tardio do instalador não invalida um binário funcional;
- token do Companion volta a ser gerado localmente por instalação em vez de vir pré-preenchido no pacote público.

### Baixe assim

- **Vortice-Setup.exe** — recomendado.
- **Vortice-Portable.zip** — portátil.
- **SHA256SUMS.txt** — hashes dos arquivos.

> O Windows pode mostrar SmartScreen enquanto o executável ainda não possui assinatura Authenticode/reputação estabelecida. Não é necessário desativar o Windows Defender.

O Vórtice é independente e não é afiliado, patrocinado ou endossado pela OpenAI.
