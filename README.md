# Voice Studio (Orochi/Silvio)

Grava a webcam e o microfone, troca a voz por IA (RVC) e gera vídeos com marca d'água avisando que a voz é
gerada por IA. Os vídeos prontos ficam em `videos_finais/` e podem ser enviados para uma pasta do Google Drive.

## Como usar

Abra pelo atalho **Voice Studio (Orochi/Silvio)** ou, no terminal, com `./iniciar_orochi_studio.sh`. O app abre na
última tomada, para você continuar de onde parou (uma gravação que falhou ao começar é pulada). Uma tomada
interrompida por travamento é recuperada sozinha, e o resultado aparece no log.

O fluxo é um clique por etapa: **Gravar** → **Parar** → **Converter** (o vídeo sai sozinho) → **Enviar**.

### 1. Gravar (coluna da esquerda)

1. Escolha a **câmera** na lista e marque **Câmera ligada**. O preview mostra a marca d'água e duas guias da coluna
   9:16. Mantenha a boca acima da faixa escura e dentro das guias: é essa coluna que sobra num corte vertical
   (Reels, TikTok, Status).
2. Escolha o **microfone**. Com **Gravar vídeo** desmarcado, o app grava só o áudio, como antes.
3. Opcional: preencha a **duração** em segundos (mínimo 1 s). Vazio = grava até você clicar **Parar**. O limite é
   5 min: aí o app para sozinho e avisa.
4. Clique **Gravar**. O indicador **REC** acende quando chega a 1ª imagem (de 0,5 a 1,2 s depois), e o fps real
   da câmera aparece ao lado.
5. Clique **Parar**. Ele só libera 1 s depois de começar e, com vídeo, depois da 1ª imagem: um clique duplo em
   **Gravar** não para a gravação. A tomada fica em `recordings/AAAA-MM-DD_HHMMSS/`. Se o som ficou quase inaudível,
   o app avisa "Microfone mudo?".

### 2. Converter e gerar o vídeo (coluna da direita)

1. **Modelo:** escolha Silvio Santos ou Orochi. **Carregar modelo** pré-aquece o conversor. É opcional: sem ele, a
   1ª conversão demora uns 10 s a mais.
2. **Aumentar volume:** se a voz ficou baixa, clique **Aumentar volume** (ganho em dB, com limitador). A conversão
   passa a usar o áudio aumentado.
3. **Converter:** troca a voz. Na pasta da tomada saem `<modelo>.wav` e `<modelo>_IA.mp3` (com o aviso de IA nas
   tags do MP3).
4. **Vídeo:** numa tomada com vídeo, o MP4 é gerado sozinho depois de converter:
   `videos_finais/AAAA-MM-DD_HHMMSS_<modelo>_IA.mp4`. Ele leva a voz convertida, a marca d'água em todos os frames
   (selo "● IA" e a faixa "VOZ GERADA POR IA") e o aviso nos metadados.
   - **Gerar vídeo** refaz o MP4 sem converter de novo (por exemplo, depois de calibrar a sincronia).
   - **Assistir** abre o vídeo; **Abrir pasta** abre `videos_finais/`.
   - Se o vídeo não passar na conferência final (frames, duração, marca d'água, metadados), nada vai para
     `videos_finais/`. O motivo aparece no log e na seção "4. Vídeo", e o arquivo parcial fica na pasta da tomada. A
     tomada continua boa: dá para clicar **Gerar vídeo** ou **Converter** de novo.
5. **Ouvir gravação** e **Ouvir resultado** tocam o áudio original e o convertido.

### 3. Enviar para o Drive

Faça antes a configuração inicial (seção "Google Drive", abaixo). Depois:

- **Enviar** manda para a pasta do Drive o vídeo da tomada e do modelo que estão na tela, com barra de progresso.
  **Cancelar** interrompe. Se ele já estiver igual no Drive, é pulado. Para mandar todos os de `videos_finais/`, use
  `python3 enviar_drive.py` no terminal (veja "5. Testar").
- **Configurar Drive** troca o link da pasta. **Reconectar** refaz o login quando ele expira.

Fechar a janela durante a gravação pede confirmação e finaliza o arquivo antes de sair ("Finalizando gravação…").
Com uma conversão, um vídeo sendo gerado ou um envio em andamento, o app também pede confirmação.

## Calibrar a sincronia (palmas)

O app alinha a voz à imagem pelo relógio do sistema (erro de até 1 frame nos testes). A câmera e o microfone, porém,
têm atrasos internos que só uma medida real mostra. A correção é o `av_offset_ms` do `estado.json`, aplicado na
hora de gerar o vídeo. Calibre uma vez, e de novo se trocar de câmera ou de microfone:

1. Se puder, deixe a câmera a 30 fps (veja "fps baixo" em "Solução de problemas") e acenda a luz.
2. Grave uma tomada **com vídeo** de uns 15 s. Deixe as mãos inteiras no quadro e bata **pelo menos 5 palmas**
   secas, com uns 2 s entre elas. Depois de cada palma, deixe as mãos juntas por um instante e só então afaste-as
   **devagar**. Não fale durante a tomada.
3. No terminal, na pasta do projeto (o app pode ficar aberto: ele não desfaz o valor salvo e lê o `av_offset_ms` na
   hora de gerar o vídeo):

   ```bash
   Applio/.venv/bin/python calibrar_av.py recordings/AAAA-MM-DD_HHMMSS
   ```

   O script mostra uma linha por palma (áudio, vídeo e a diferença) e, no fim, algo como:

   ```text
   av_offset_ms sugerido: -80 ms (mediana de 6 palmas; dispersão ±1,9 ms; de -82 a -78)
   Positivo = áudio mais tarde. A medida é feita sem offset: o valor substitui o atual.
   Confiança: alta
   ```

4. Se a confiança for **alta** ou **média**, grave o valor:

   ```bash
   Applio/.venv/bin/python calibrar_av.py recordings/AAAA-MM-DD_HHMMSS --salvar
   ```

   Ele só grava com pelo menos 5 palmas vistas no vídeo e confiança alta ou média. Com confiança baixa, ele recusa
   e não mexe no `estado.json`.
5. Para conferir, grave uma tomada nova falando, converta e assista ao vídeo. O novo offset vale para os vídeos
   gerados daqui em diante. Um vídeo antigo só muda se você clicar **Gerar vídeo** com a tomada dele aberta.

- **Confiança baixa:** poucas palmas vistas, ou palmas discordando entre si (dispersão acima de 15 ms, ou menos de
  80 % delas perto da mediana). Refaça com mais luz, as mãos inteiras no quadro e palmas mais secas, parando as mãos
  depois de cada uma. Palmas muito juntas também são descartadas: deixe uns 2 s entre elas.
- **Confiança média:** a câmera estava abaixo de 20 fps. O valor serve, mas a 30 fps a medida fica mais firme.
- **Aviso de offset acima de 200 ms:** câmera e microfone USB costumam ficar abaixo disso. Confira as palmas na
  tabela e, na dúvida, grave outra tomada. O aviso não muda a confiança: um atraso real desse tamanho pode ser salvo.
- Para voltar ao padrão, ponha `"av_offset_ms": 0` no `estado.json`. Se o arquivo ficar com erro de sintaxe, o app
  avisa, usa os padrões e guarda o arquivo quebrado em `estado.json.corrompido`.
- O script não altera a tomada. Depois de calibrar, ela pode ser apagada.

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
- Sem `--arquivo`, a decisão de reenviar ou não é do **histórico local** (`take.json`), não do que está hoje na
  pasta do Drive: um vídeo cujo envio já foi registrado (mesmo MD5 de antes) aparece como "pulado (já enviado
  antes)" e **nem chega a chamar o rclone**. Isso vale mesmo que a pasta seja de outra pessoa e ela tenha
  movido, renomeado ou apagado o arquivo lá — o script não sabe disso (e não deveria adivinhar) e não vai
  recriar nem duplicar o vídeo por causa disso. Use `--reenviar` para ignorar esse histórico e reenviar tudo de
  `videos_finais/` de qualquer forma (nesse caso quem decide se duplica ou não volta a ser o rclone, como
  antes). `--arquivo` sempre envia o que foi pedido, mesmo que já esteja registrado como enviado.
- Depois de cada envio, o script confere o tamanho e o MD5 do arquivo no Drive com o arquivo local.
- Só sobem vídeos `*_IA.mp4` com o aviso de IA nos metadados. Arquivos `.part` nunca sobem.
- Um envio por vez: se o app estiver enviando, o script avisa "Outro envio já está em andamento".
- Código de saída: `0` = tudo enviado ou pulado; `1` = alguma falha; `2` = pasta não configurada ou link
  inválido.
- Pode ir para o cron, por exemplo a cada hora:
  `0 * * * * cd ~/orochi-ia-homenagem && python3 enviar_drive.py >> envio_drive.log 2>&1`. Rodar isso toda
  hora é seguro: o histórico local garante que cada vídeo só é enviado uma vez, mesmo que a pasta de destino
  mude de conteúdo por fora (outra pessoa apagando ou movendo arquivos lá).

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

## Solução de problemas

O log embaixo, no app, mostra uma linha curta por problema. Os detalhes técnicos ficam em `studio.log` (e em
`studio_rvc.log`, para o conversor de voz).

### "Câmera em uso por outro programa (Meet/Zoom/OBS?)"

Só um programa por vez pode usar a câmera. Feche a chamada ou o OBS, ou só desligue a câmera neles. Para ver quem
está com ela:

```bash
fuser -v /dev/video0
```

O próprio preview do app também segura a câmera. Ele desliga sozinho quando a janela é minimizada e depois de
5 min sem uso; desmarque **Câmera ligada** para liberar a câmera na hora.

- "Câmera não encontrada": a câmera foi desconectada. Reconecte e escolha de novo na lista.
- "Dispositivo de vídeo errado": escolha a entrada que termina em `-video-index0`.
- "A câmera parou de enviar imagem": problema no cabo ou na porta USB. A gravação é encerrada, e o que já foi
  gravado fica salvo.

### "Microfone mudo?" ou "Microfone desconectado ou trocado"

- **"Microfone mudo?"** aparece quando o pico da gravação fica abaixo de −45 dB.
  - Confira se o microfone certo está escolhido no app.
  - Confira se ele não está mudo ou com volume zero no sistema: Configurações → Som → Entrada, ou o `pavucontrol`
    (aba "Dispositivos de entrada"). No terminal, `pactl list short sources` lista os microfones, e
    `pactl get-source-mute NOME` tem que responder "não" (ou "no").
  - Se a voz só ficou baixa, e não muda, use **Aumentar volume** antes de converter.
- **"Microfone desconectado ou trocado"**: durante a tomada, o microfone sumiu ou o sistema passou a gravar de outro
  (o app confere a cada 2 s e para na 2ª vez seguida). Reconecte, escolha o microfone de novo e grave outra vez.
- **"Não deu para conferir o microfone (o pactl não respondeu)"**: só um aviso; a gravação continua. Se aparecer
  sempre, confira se o som do sistema está funcionando (`pactl info`).

### fps baixo (câmera a ~15 fps)

A câmera A4tech entrega ~15 fps com o ajuste "Exposure, Dynamic Framerate" ligado, que é como ela está hoje.
Desligado, ela faz ~30 fps. Com pouca luz, o fps cai ainda mais, e o app avisa "Câmera a X fps (abaixo de 12) —
pouca luz?". O app mostra o fps real, mas não mexe nesse ajuste.

- A sincronia continua certa a 15 fps: o vídeo final sai sempre a 30 fps, repetindo frames. Só o movimento da boca
  fica mais "aos saltos".
- Primeiro, ponha mais luz na frente do rosto.
- Para ter 30 fps, desligue o ajuste com o `v4l2-ctl`, com o app fechado. Estes comandos **não foram testados nesta
  máquina** e precisam do pacote `v4l-utils` (`sudo apt install v4l-utils`):

  ```bash
  CAM=/dev/v4l/by-id/usb-Sonix_Technology_Co.__Ltd._A4tech_HD_720P_PC_Camera_SN0001-video-index0
  v4l2-ctl -d "$CAM" --list-ctrls | grep -i dynamic     # mostra o valor atual (1 = ligado)
  v4l2-ctl -d "$CAM" -c exposure_dynamic_framerate=0    # 30 fps; com pouca luz a imagem fica mais escura
  v4l2-ctl -d "$CAM" -c exposure_dynamic_framerate=1    # volta como estava
  ```

  O valor pode mudar quando a câmera é reconectada ou quando outro programa mexe nele. Confira com a 1ª linha.

Sem instalar nada, o mesmo ajuste muda pelo `ioctl` que o probe `docs/superpowers/probes/2026-09-26/captura/t10_fps.py`
usou para medir os 29,3 fps (`VIDIOC_S_CTRL` no controle `0x9a0903`, "Exposure, Dynamic Framerate"; valor `0` =
~30 fps, `1` = como está hoje). Com o app fechado e o `CAM` acima (troque o `0` por `1` para voltar):

```bash
python3 - "$CAM" 0 <<'EOF'
import fcntl, os, struct, sys
CID = 0x9a0903                              # "Exposure, Dynamic Framerate"
S_CTRL, G_CTRL = 0xC008561C, 0xC008561B     # VIDIOC_S_CTRL e VIDIOC_G_CTRL (como no probe t10_fps.py)
fd = os.open(sys.argv[1], os.O_RDWR | os.O_NONBLOCK)
try:
    fcntl.ioctl(fd, S_CTRL, bytearray(struct.pack("<Ii", CID, int(sys.argv[2]))))
    c = bytearray(struct.pack("<Ii", CID, 0))
    fcntl.ioctl(fd, G_CTRL, c)
    print("Exposure, Dynamic Framerate =", struct.unpack("<Ii", c)[1])
finally:
    os.close(fd)
EOF
```

A última linha mostra o valor que ficou.

### Drive: login expirou ou Drive cheio

- **"Login do Drive expirou — clique Reconectar"**: clique **Reconectar**, que abre o navegador para entrar de novo.
  No terminal, o equivalente é `rclone config reconnect iavoz:`. Se isso acontece toda semana, o app OAuth ficou
  em modo "Teste": faça o passo 2.7 da configuração do Drive (**PUBLICAR APP**).
- **"Seu Drive está cheio — os envios contam na sua cota"**: os vídeos contam na cota da **sua** conta Google
  (15 GB grátis, ~45 MB por minuto de vídeo), e não na do dono da pasta. Libere espaço (o uso aparece em
  **Armazenamento**, no menu do Drive, e a lixeira também conta) ou use uma conta com mais espaço. Depois clique
  **Enviar** de novo: o que já subiu é pulado.
- As outras mensagens do Drive estão na tabela "Mensagens de erro", na seção do Drive.
