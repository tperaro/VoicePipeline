from fontTools.ttLib import TTFont
fonts = {
 "DejaVuSans-Bold": "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
 "DejaVuSans": "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
 "NotoSans-Bold": "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
 "NotoSans-Regular": "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
 "LiberationSans-Bold": "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
 "LiberationSans-Regular": "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
 "Ubuntu-B": "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf",
 "Ubuntu-R": "/usr/share/fonts/truetype/ubuntu/Ubuntu-R.ttf",
}
chars = "ãéóçÓÃÉÇõêâí·●◉•"
for name, path in fonts.items():
    cmap = TTFont(path).getBestCmap()
    miss = [c for c in chars if ord(c) not in cmap]
    print(f"{name:24s} missing: {''.join(miss) or '-'}")
