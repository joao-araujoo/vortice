# Publicando o Vórtice no GitHub

O repositório oficial esperado por este pacote é:

`https://github.com/joao-araujoo/vortice`

## Jeito mais fácil

> Compatibilidade: os scripts `.ps1` deste pacote usam somente ASCII de proposito, para funcionar tambem no Windows PowerShell 5.1 sem depender de BOM/UTF-8.



> Se uma tentativa anterior criou `joao-araujoo/vortice` mas parou antes do push, pode executar o `.bat` novamente. O publicador detecta o repositório existente, recria/repara o `origin` e continua sem você precisar apagar nada.

Na raiz do projeto execute:

```bat
PUBLISH-GITHUB.bat
```

O script:

1. confere Git e GitHub CLI;
2. autentica sua conta com `gh auth login` se necessário;
3. inicializa o Git;
4. cria `joao-araujoo/vortice` como repositório público se ainda não existir;
5. faz o primeiro commit e push;
6. configura descrição/topics;
7. cria e envia a tag `v4.4.1`;
8. a tag dispara `.github/workflows/release.yml`;
9. o GitHub gera `Vortice-Setup.exe`, `Vortice-Portable.zip` e checksums automaticamente.

## Comandos manuais equivalentes

```powershell
winget install --id GitHub.cli -e

gh auth login --web --git-protocol https

git init
git branch -M main
git add .
git commit -m "chore: Vórtice 4.4.1 Public Beta"

gh repo create vortice --public `
  --description "Vórtice — orquestrador desktop local-first para desenvolvimento com Tasks, contexto, backups e ChatGPT."
# Se o repositório já existir, ignore a mensagem acima e continue.

if (git remote | Select-String '^origin$') {
  git remote set-url origin https://github.com/joao-araujoo/vortice.git
} else {
  git remote add origin https://github.com/joao-araujoo/vortice.git
}
git push -u origin main

gh repo edit joao-araujoo/vortice --enable-issues=true --enable-wiki=false `
  --add-topic vortice --add-topic developer-tools --add-topic chatgpt --add-topic codex --add-topic windows --add-topic productivity

git tag -a v4.4.1 -m "Vórtice 4.4.1 Public Beta"
git push origin v4.4.1
```

Depois acompanhe:

- Actions: `https://github.com/joao-araujoo/vortice/actions`
- Releases: `https://github.com/joao-araujoo/vortice/releases`

Os botões do README começam a funcionar assim que o primeiro workflow de Release terminar.
