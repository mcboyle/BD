#!/usr/bin/env python3
"""Generate the 30-theme CSS block for BulkDownloader's app.css.

Each theme is defined as a compact palette, then expanded to the app's
FULL :root token set so every component themes correctly -- surfaces,
the green/amber/red/blue semantic triples, shadows, glow, fonts.

Output is a CSS string appended to app.css inside clear sentinels so it
can be regenerated. Run: python gen_themes.py  ->  prints CSS to stdout.
"""

# palette = (key, name, dark?, bg0, bg1, bg2, bg3, border, border2,
#            text, muted, muted2, primary, primary_dim, primary_bg,
#            green, amber, red, blue, font, mono)
# Fonts: 'i'=Inter 's'=Sora 'p'=IBM Plex Sans 'f'=Libre Franklin
#        'r'=Fraunces(serif) 'j'=JetBrains Mono 'x'=IBM Plex Mono
P = [
 ("console","Console",1,"#0c0d0f","#141518","#1c1d21","#26272c","#26272c",
  "#34353c","#d6d8dd","#7d7f88","#a7a9b2","#ffb020","#b87b14","#241a0d",
  "#9bd83a","#ffb020","#ff5757","#56a8ff","p","x"),
 ("editorial","Editorial",0,"#f4f1ea","#fffdf8","#f1ece0","#e9e3d4",
  "#e6e0d2","#d8d0bd","#2a2622","#857d70","#5c5650","#bf4d2c","#9a3c20",
  "#f6e3da","#5c7355","#b8893a","#bf4d2c","#3b6ea5","f","x"),
 ("midnight","Midnight",1,"#080b15","#10162a","#1a2036","#252c48",
  "#222a44","#333d60","#e4e8f4","#8b93b4","#aab2d0","#34e3d4","#1f9d92",
  "#0e2b35","#34e3d4","#ffc24b","#ff6b8a","#7aa2f7","s","j"),
 ("nord","Nord",1,"#2e3440","#3b4252","#434c5e","#4c566a","#434c5e",
  "#4c566a","#eceff4","#a7b0c0","#c8ced8","#88c0d0","#5e81ac","#3b4a5a",
  "#a3be8c","#ebcb8b","#bf616a","#81a1c1","p","x"),
 ("solarized","Solarized",1,"#002b36","#073642","#0b4856","#155a68",
  "#0c4a58","#155a68","#a7b6b6","#7a9091","#93a1a1","#268bd2","#1a6ba0",
  "#0a3d4a","#859900","#b58900","#dc322f","#268bd2","p","x"),
 ("paper","Paper",0,"#ffffff","#ffffff","#f3f5f8","#eef1f5","#e7e9ee",
  "#d5d9e1","#14171f","#6b7280","#4b5563","#2563eb","#1d4ed8","#eef2ff",
  "#059669","#d97706","#dc2626","#2563eb","f","x"),
 ("monochrome","Monochrome",1,"#0f0f0f","#181818","#242424","#2e2e2e",
  "#262626","#383838","#ededed","#8a8a8a","#b4b4b4","#ededed","#9a9a9a",
  "#2a2a2a","#cdcdcd","#9a9a9a","#7a7a7a","#bcbcbc","f","x"),
 ("terminal","Terminal",1,"#050a06","#08160b","#0d2110","#123018",
  "#123018","#1c4a26","#5bff8f","#2f9a52","#42c46e","#33ff66","#1f9a3e",
  "#0a2412","#33ff66","#ffe14d","#ff5a5a","#4dd0ff","j","j"),
 ("ember","Ember",1,"#14100e","#1f1813","#2a201a","#352920","#2e241c",
  "#3e2f24","#f0e6dc","#b09a86","#cdb9a6","#ff7a4d","#c25a32","#2e1c12",
  "#8fbf6a","#ffb547","#ff5d5d","#ffa46a","s","j"),
 ("forest","Forest",1,"#0d1410","#131f18","#1a2c20","#22382a","#1e3326",
  "#2b4a36","#e3ede4","#8fa896","#aec0b4","#4ade80","#2f9d5b","#10240f",
  "#4ade80","#facc6b","#f87171","#6ee7b7","f","j"),
 ("rose","Rose Pine",1,"#191724","#1f1d2e","#26233a","#312e48","#2a2740",
  "#3a3658","#e0def4","#908caa","#b3aed0","#ebbcba","#c98a88","#2a2030",
  "#9ccfd8","#f6c177","#eb6f92","#c4a7e7","s","j"),
 ("crimson","Crimson",1,"#100808","#1b0e0e","#261414","#332020","#2e1818",
  "#4a2424","#f3e4e4","#b89494","#d0adad","#f5333f","#b8242d","#2a1010",
  "#4fb286","#f5a623","#f5333f","#5a9bd4","p","x"),
 ("daylight","Daylight",0,"#f3f5f8","#ffffff","#eef1f5","#e6eaf0",
  "#e2e6ec","#cfd5de","#1b2430","#5e6878","#3b4554","#0e9e96","#0a7a73",
  "#e0f4f2","#0e9e96","#d98c1f","#d6453f","#2b7fd0","i","j"),
 ("neon","Neon",1,"#07060d","#110d1f","#1a1430","#241c44","#241a44",
  "#382a66","#ece6ff","#9588c4","#b6abdc","#ff2bd1","#c01f9e","#2a0a24",
  "#2bf0ff","#ffd23f","#ff3b6b","#7c5cff","s","j"),
 ("dracula","Dracula",1,"#282a36","#2d2f3d","#383a4d","#42445a","#383a4d",
  "#44475a","#f8f8f2","#9ea3c4","#c4c8e0","#bd93f9","#9166d8","#2a2440",
  "#50fa7b","#f1fa8c","#ff5555","#8be9fd","s","j"),
 ("gruvbox","Gruvbox",1,"#282828","#32302f","#3c3836","#45403d","#3c3836",
  "#504945","#ebdbb2","#a89984","#bdae93","#fabd2f","#d79921","#3a3024",
  "#b8bb26","#fe8019","#fb4934","#83a598","p","x"),
 ("tokyo","Tokyo Night",1,"#1a1b26","#1f2335","#292e42","#333854","#292e42",
  "#3b4261","#c0caf5","#828bb8","#a9b1d6","#7aa2f7","#5a7fce","#1c2336",
  "#9ece6a","#e0af68","#f7768e","#7aa2f7","s","j"),
 ("mocha","Catppuccin Mocha",1,"#1e1e2e","#28283c","#313244","#3a3b50",
  "#313244","#45475a","#cdd6f4","#a6adc8","#bac2de","#cba6f7","#a37fd6",
  "#2a2440","#a6e3a1","#f9e2af","#f38ba8","#89b4fa","s","j"),
 ("latte","Catppuccin Latte",0,"#eff1f5","#ffffff","#e6e9ef","#dce0e8",
  "#dce0e8","#ccd0da","#4c4f69","#6c6f85","#5c5f77","#8839ef","#7022c8",
  "#f0e7fc","#40a02b","#df8e1d","#d20f39","#1e66f5","f","x"),
 ("onedark","One Dark",1,"#282c34","#2f343d","#3a3f4b","#444a58","#3a3f4b",
  "#4b5263","#d7dae0","#9aa0ac","#b6bcc8","#61afef","#4a8fd0","#22303f",
  "#98c379","#e5c07b","#e06c75","#61afef","p","x"),
 ("monokai","Monokai",1,"#272822","#31322a","#3b3c33","#45463c","#3b3c33",
  "#49483e","#f8f8f2","#a59f85","#c4bfa8","#a6e22e","#7fae22","#2c3320",
  "#a6e22e","#fd971f","#f92672","#66d9ef","p","x"),
 ("everforest","Everforest",1,"#2d353b","#343f44","#3d484d","#475157",
  "#3d484d","#4f585e","#d3c6aa","#9da9a0","#b8c0b4","#a7c080","#83a065",
  "#2e3a32","#a7c080","#dbbc7f","#e67e80","#7fbbb3","f","j"),
 ("ayu","Ayu Mirage",1,"#1f2430","#232834","#2d3340","#373d4c","#2d3340",
  "#3d4350","#cbccc6","#8a909c","#b0b4ba","#ffcc66","#d9a648","#33302a",
  "#a6cc70","#ffad66","#f28779","#73d0ff","s","j"),
 ("sepia","Sepia",0,"#f1e7d3","#f8f1e2","#ece0c6","#e4d7b8","#e0d2b8",
  "#cdb897","#433422","#8a7559","#6a5942","#a06a2c","#7d5120","#efe2c8",
  "#6b7d4f","#b8893a","#a5482c","#5a7a9a","f","x"),
 ("steel","Steel",1,"#0f1620","#162130","#1f2d40","#28384e","#1f2d40",
  "#2e4156","#dde6f0","#8696aa","#aab8c8","#3b82f6","#2563cc","#142840",
  "#22c55e","#f59e0b","#ef4444","#60a5fa","p","x"),
 ("mint","Mint",0,"#eef6f1","#ffffff","#e9f4ee","#e0efe7","#dce9e2",
  "#c6dcd0","#16302a","#5a7268","#3e5650","#0f9d6b","#0a7a52","#dcf3e9",
  "#0f9d6b","#d98c1f","#dc4b3f","#2b8fb0","f","j"),
 ("wine","Wine",1,"#1a0c12","#26121c","#341a28","#3f2433","#3a1e2c",
  "#522b3e","#f0e2e8","#bd9aa8","#d4bbc6","#d4a843","#a8842f","#2e1822",
  "#7ab38a","#d4a843","#e0607a","#b08aa8","s","j"),
 ("synthwave","Synthwave",1,"#1a0b2e","#241241","#321a55","#3d2266",
  "#3a2168","#52308f","#f4e9ff","#b39ad6","#cdb8e8","#ff4d8d","#cc2f6a",
  "#2e1844","#36e0c8","#ffb43d","#ff4d6d","#9d7bff","s","j"),
 ("carbon","Carbon",1,"#161616","#1c1c1c","#262626","#303030","#262626",
  "#393939","#f4f4f4","#a8a8a8","#c6c6c6","#0f62fe","#0043ce","#16213c",
  "#42be65","#f1c21b","#fa4d56","#4589ff","p","x"),
]

FONTS = {
 "i": "'Inter','Sora',system-ui,-apple-system,sans-serif",
 "s": "'Sora','Inter',system-ui,-apple-system,sans-serif",
 "p": "'IBM Plex Sans',system-ui,-apple-system,sans-serif",
 "f": "'Libre Franklin',system-ui,-apple-system,sans-serif",
 "r": "'Fraunces',Georgia,serif",
 "j": "'JetBrains Mono',ui-monospace,Menlo,Consolas,monospace",
 "x": "'IBM Plex Mono',ui-monospace,Menlo,Consolas,monospace",
}


def hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def mix(c, bg, alpha):
    """Flatten color c at `alpha` over bg -> opaque hex (for *bg tints)."""
    cr = hex_to_rgb(c)
    br = hex_to_rgb(bg)
    out = tuple(round(cr[i] * alpha + br[i] * (1 - alpha)) for i in range(3))
    return "#%02x%02x%02x" % out


def darken(c, f):
    r = hex_to_rgb(c)
    return "#%02x%02x%02x" % tuple(round(x * f) for x in r)


def build():
    blocks = []
    for (key, name, dark, bg0, bg1, bg2, bg3, border, border2, text,
         muted, muted2, primary, pdim, pbg, green, amber, red, blue,
         font, mono) in P:
        # semantic dim/bg derived from the base hue so every triple is
        # consistent without hand-tuning 30x5 values
        sh = ("0,0,0,.5" if dark else "50,50,93,.10")
        glow_rgb = ",".join(str(x) for x in hex_to_rgb(primary))
        decls = {
            "--bg0": bg0, "--bg1": bg1, "--bg2": bg2, "--bg3": bg3,
            "--border": border, "--border2": border2,
            "--text": text, "--muted": muted, "--muted2": muted2,
            "--primary": primary, "--primary-dim": pdim,
            "--primary-bg": pbg,
            "--green": green, "--gdim": darken(green, .55),
            "--gbg": mix(green, bg1, .16 if dark else .14),
            "--amber": amber, "--adim": darken(amber, .5),
            "--abg": mix(amber, bg1, .16 if dark else .14),
            "--red": red, "--rdim": darken(red, .5),
            "--rbg": mix(red, bg1, .16 if dark else .14),
            "--blue": blue, "--bdim": darken(blue, .5),
            "--bbg": mix(blue, bg1, .16 if dark else .14),
            "--shadow-sm": f"0 1px 2px rgba({sh})",
            "--shadow-md": f"0 4px 12px rgba({sh})",
            "--shadow-lg": f"0 12px 32px rgba({sh})",
            "--shadow-xl": f"0 24px 64px rgba({sh})",
            "--glow-primary": f"0 0 0 3px rgba({glow_rgb},.20)",
            "--font": FONTS[font], "--mono": FONTS[mono],
        }
        body = ";\n  ".join(f"{k}:{v}" for k, v in decls.items())
        blocks.append(f'[data-theme="{key}"]{{\n  /* {name} */\n  {body};\n}}')
    return "\n".join(blocks)


if __name__ == "__main__":
    print("/* === BD-THEMES-GENERATED-START === */")
    print("/* 30 themes. Generated by tools/gen_themes.py -- each maps")
    print("   the app's full :root token set. Regenerate, don't hand-edit. */")
    print(build())
    print("/* === BD-THEMES-GENERATED-END === */")
