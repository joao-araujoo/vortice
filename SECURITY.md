# Segurança

## Reportando uma vulnerabilidade

Não publique credenciais, tokens, chaves ou conteúdo privado em Issues.

Para falhas que envolvam apenas comportamento local não sensível, abra uma Issue com passos de reprodução e versão do Vórtice.

## Proteções existentes

- filtros de caminhos e nomes de arquivos sensíveis ao preparar contexto;
- limites de tamanho;
- validação de ZIP antes de aplicar;
- identidade de Task/Rodada via RESULT_TOKEN;
- backups de código antes da aplicação;
- rollback para fluxos compatíveis;
- dados do usuário fora da pasta do executável.

## Limitações

O Vórtice é Public Beta. Use Git, backups externos e revisão humana em projetos importantes. Nenhuma validação local substitui controle de versão ou revisão de segurança.
