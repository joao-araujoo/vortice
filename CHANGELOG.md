# Changelog

## 4.4.2 — Public Beta

- reparo da instalação/detecção do Codex no Windows;
- validação de cada candidato com `codex --version`;
- suporte a instalação já existente fora do PATH atual do Explorer;
- recuperação quando o instalador standalone escreve `codex.exe` mas falha no self-check final;
- fallback npm somente quando npm já estiver instalado;
- normalização de caminhos com aspas e PATH do processo atualizado em runtime;
- botão de diagnóstico do Codex;
- `INSTALL-CODEX.bat` resiliente ao mesmo cenário;
- Companion token removido do estado padrão distribuído.

## 4.4.1 — Public Beta

- onboarding de primeira execução passou a configurar a Codex CLI;
- instalação standalone oficial pelo próprio Vórtice;
- login com ChatGPT e `codex doctor` acessíveis pela interface;
- detecção ampliada de caminhos do Codex no Windows;
- fallback manual para localizar `codex.exe`;
- nova aba Config → Dependências;
- status do Codex no header abre o reparador;
- tela pública de Automação simplificada para não sugerir controles indisponíveis;
- publicador passou a preservar o histórico remoto ao atualizar um repositório já existente.

## 4.4.0 — Public Beta

- primeira distribuição preparada para GitHub Releases;
- README/branding públicos, documentação, privacidade, segurança e contribuição;
- build reproduzível em Windows via GitHub Actions;
- instalador Inno Setup + versão portátil;
- metadados de versão do executável;
- onboarding inicial para novos usuários;
- edição pública fixada no handoff Assistido do ChatGPT Web;
- screenshots e diagramas de arquitetura/uso de Codex;
- script `PUBLISH-GITHUB.bat` para criar o repositório, subir o código e disparar a primeira Release;
- identidade visual pública atualizada com os novos logos oficiais e ícone do executável.

## 4.3.0

- identidade forte por Task/Rodada;
- visão do projeto, checklist, homologação e timeline;
- validações locais, memória técnica e glossário de ditado;
- melhorias de segurança e organização do fluxo.
