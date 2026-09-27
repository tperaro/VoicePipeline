# Voice Studio (Orochi/Silvio)

Grava a webcam e o microfone, troca a voz por IA (RVC) e gera vídeos com marca d'água avisando que a voz é
gerada por IA. Os vídeos prontos ficam em `videos_finais/` e podem ser enviados para uma pasta do Google Drive.

## Google Drive: configuração inicial

Isto é feito **uma vez só**. Depois, o botão **Enviar** do app e o script `enviar_drive.py` usam essa
configuração.

O envio usa o [rclone](https://rclone.org) com uma credencial OAuth **sua** no Google Cloud (o client
compartilhado do rclone deixa de funcionar durante 2026). Os vídeos vão para uma pasta que alguém compartilhou
com você com permissão de **edição**.

### 1. Instalar o rclone v1.75.1

O rclone do `apt` (1.60) é velho demais. Instale o binário oficial em `~/.local/bin` (não precisa de sudo):

```bash
cd "$(mktemp -d)"
curl -fLO https://downloads.rclone.org/v1.75.1/rclone-v1.75.1-linux-amd64.zip
curl -fLO https://downloads.rclone.org/v1.75.1/SHA256SUMS
grep ' rclone-v1.75.1-linux-amd64.zip$' SHA256SUMS | sha256sum -c -
mkdir -p ~/.local/bin && unzip -j rclone-v1.75.1-linux-amd64.zip 'rclone-v1.75.1-linux-amd64/rclone' -d ~/.local/bin && chmod 755 ~/.local/bin/rclone
rclone version
```

- O `sha256sum -c` tem que responder `rclone-v1.75.1-linux-amd64.zip: SUCESSO` (ou `: OK`, em inglês). Se
  der `FALHOU`, apague o zip e baixe de novo; **não instale**.
- Para conferência extra, o SHA256 do zip conferido em 26/09/2026 é
  `982b5aa772841168f8e380f139e9e787b2a105403e32b94da8676a0e1c0a13ab`.
- Opcional, mais forte: conferir a assinatura PGP do `SHA256SUMS` (a chave tem que ser
  `FBF737ECE9F8AB18604BD2AC93935E02FF3B54FA`, de Nick Craig-Wood; o mesmo valor aparece em
  `dig key.rclone.org txt`):

  ```bash
  gpg --keyserver keyserver.ubuntu.com --receive-keys FBF737ECE9F8AB18604BD2AC93935E02FF3B54FA
  gpg --verify SHA256SUMS
  ```

  O `gpg` tem que dizer "Assinatura correta" (ou "Good signature") com essa chave.
- Se `rclone version` disser "comando não encontrado", abra um terminal novo (o `~/.local/bin` entra no PATH no
  login). O app e o script acham o rclone em `~/.local/bin` mesmo sem ele no PATH.

### 2. Criar a credencial OAuth no Google Cloud

Faça tudo logado com a conta Google que tem **edição** na pasta (veja a recomendação de conta dedicada em
"Segurança", abaixo). Os nomes dos menus podem aparecer em português ou em inglês.

1. Abra <https://console.cloud.google.com/> e crie um projeto (por exemplo, "voice-studio-drive").
2. **APIs e serviços → Biblioteca** ("APIs & Services → Library"): procure "Google Drive API" e clique em
   **Ativar** ("Enable").
3. **Tela de permissão OAuth** ("OAuth consent screen" → "Get started"):
   - Nome do app: "Voice Studio"; e-mail de suporte: o seu.
   - Público-alvo ("Audience"): **Externo** ("External").
   - Informações de contato: o seu e-mail. Aceite os termos e crie.
4. **Acesso a dados** ("Data Access") → **Adicionar ou remover escopos** ("Add or remove scopes"): adicione
   `https://www.googleapis.com/auth/drive` e clique em **Salvar** ("Save").
5. **Público-alvo** ("Audience") → **Usuários de teste** ("Test users") → adicione o seu e-mail.
6. **Clientes** ("Clients") → **Criar cliente** ("Create OAuth client") → tipo **App para computador**
   ("Desktop app") → Criar. Anote o **ID do cliente** (client ID) e a **chave secreta** (client secret).
7. **Público-alvo** ("Audience") → **PUBLICAR APP** ("PUBLISH APP") → Confirmar. **Não pule este passo:** em
   modo "Teste" ("Testing") o login expira a cada 7 dias.
   - Se o botão estiver cinza, o Google pede antes, em **Branding**, uma "página inicial do app" e um "link da
     política de privacidade" (uma página simples no GitHub Pages serve).
   - Não precisa mandar o app para verificação. No login vai aparecer "O Google não verificou este app"; para
     uso pessoal isso é esperado.

### 3. Criar o remote `iavoz`

No terminal (troque pelos valores do passo 2.6):

```bash
rclone config create iavoz drive client_id=SEU_CLIENT_ID client_secret=SUA_CHAVE_SECRETA scope=drive
```

- O navegador abre sozinho. Escolha a conta, clique em **Avançado → Acessar Voice Studio (não seguro)**
  ("Advanced → Go to … (unsafe)") e permita o acesso ao Drive. O comando termina quando a página mostrar
  "Success".
- Se o navegador não abrir, copie o link `http://127.0.0.1:53682/auth?...` que o rclone mostra. A porta 53682
  precisa estar livre.
- `scope=drive` é obrigatório: com `drive.file` o rclone não consegue gravar numa pasta que ele não criou.
- **Não** coloque `root_folder_id` aqui. A pasta vai em cada envio (vem do `estado.json`), então dá para trocar de
  pasta sem fazer login de novo.
- A chave secreta fica visível no histórico do shell. Ela não é um segredo forte para app de desktop, mas não a
  coloque no git. Dica: comece o comando com um espaço para ele não ir para o histórico.

### 4. Colar o link da pasta

No Drive, abra a pasta compartilhada com você → **Compartilhar → Copiar link**. Depois:

- no app: **Configurar Drive** e cole o link; ou
- no terminal: `python3 enviar_drive.py --pasta 'LINK_DA_PASTA' --dry-run`

O link fica salvo em `estado.json`. São aceitos links `…/folders/<ID>`, `…/u/0/folders/<ID>`, `open?id=`,
`folderview?id=`, com `resourcekey`, e o ID puro. Links de **arquivo** (`/file/d/…`) são recusados.

### 5. Testar

```bash
rclone lsjson iavoz: --drive-root-folder-id ID_DA_PASTA --max-depth 1; echo "saída: $?"
```

Tem que listar o conteúdo da pasta (`[]` se estiver vazia) e terminar com `saída: 0`. O `ID_DA_PASTA` é o trecho
depois de `/folders/` no link. Depois:

```bash
python3 enviar_drive.py --dry-run     # mostra o que seria enviado, sem enviar nada
python3 enviar_drive.py               # envia todos os videos_finais/*_IA.mp4
python3 enviar_drive.py --arquivo videos_finais/2026-09-26_101500_silvio_IA.mp4
```

- Rodar de novo **não duplica** nada: o que já está igual no Drive aparece como "pulado". Se um vídeo mudou, ele
  vira uma nova versão do mesmo arquivo no Drive.
- Depois de cada envio, o script confere o tamanho e o MD5 do arquivo no Drive com o arquivo local.
- Só sobem vídeos `*_IA.mp4` com o aviso de IA nos metadados. Arquivos `.part` nunca sobem.
- Um envio por vez: se o app estiver enviando, o script avisa "Outro envio já está em andamento".
- Código de saída: `0` = tudo enviado ou pulado; `1` = alguma falha; `2` = pasta não configurada ou link
  inválido.
- Pode ir para o cron, por exemplo a cada hora:
  `0 * * * * cd ~/orochi-ia-homenagem && python3 enviar_drive.py >> envio_drive.log 2>&1`

### Mensagens de erro

| Mensagem | O que fazer |
|---|---|
| rclone não instalado — veja o README | Passo 1. |
| Drive não configurado — rode a configuração | Passo 3 (o remote `iavoz` não existe). |
| Login do Drive expirou — clique Reconectar | No app: **Reconectar** (abre o navegador). No terminal: `rclone config reconnect iavoz:`. Se acontece toda semana, o app ficou em "Teste": faça o passo 2.7. |
| Essa conta Google não tem acesso a essa pasta (ou o link está errado) | Confira o link (passo 4) e se a pasta foi compartilhada com **edição** com a conta usada no passo 3. |
| Seu Drive está cheio — os envios contam na sua cota | Os vídeos contam na cota da **sua** conta (15 GB grátis, ~45 MB por minuto de vídeo), não na do dono da pasta. |
| Falha no envio (código N) + últimas linhas do log | Erro de rede ou do Google; tente de novo mais tarde. |

### Segurança e onde fica o token

- O login fica em `~/.config/rclone/rclone.conf` (modo 0600), **fora** do projeto. Nunca copie esse arquivo
  para a pasta do projeto nem use `--config` apontando para cá.
- Com `scope=drive`, esse token acessa o Drive **inteiro** da conta. Recomendação (não obrigatória): use uma
  conta Google **dedicada**, que só tenha acesso à pasta compartilhada.
- Para mostrar a configuração a alguém, use `rclone config redacted` (esconde o token), nunca
  `rclone config show`.
- O app e o script nunca apagam nem movem nada no Drive: só usam `rclone copyto` e `rclone lsjson`.
- Para revogar o acesso: <https://myaccount.google.com/permissions> → remova o app; e apague o remote com
  `rclone config delete iavoz`.
