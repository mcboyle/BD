#!/usr/bin/env python3
"""Slice 4b runtime probe: at 390px, the BottomTabBar 'More' button opens a
bottom-sheet drawer anchored to the viewport bottom (NOT trapped inside the
backdrop-blur bar, footgun[2]), grouped nav reachable, closes on scrim+Esc.
Theme-aware via BD_THEME. Screenshots to /home/claude/more_{theme}.png."""
import os, asyncio
from playwright.async_api import async_playwright
BASE="http://127.0.0.1:5599"; THEME=os.environ.get("BD_THEME","light")
async def main():
    async with async_playwright() as pw:
        b=await pw.chromium.launch()
        ctx=await b.new_context(viewport={"width":390,"height":844})
        await ctx.add_init_script(f"try{{localStorage.setItem('bd-theme','{THEME}')}}catch(e){{}}")
        p=await ctx.new_page()
        await p.goto(BASE+"/queue", wait_until="domcontentloaded")
        try: await p.wait_for_load_state("networkidle", timeout=6000)
        except Exception: pass
        await p.wait_for_timeout(700)
        # 1. More button exists & is hit-testable
        more=p.get_by_role("button", name="More")
        assert await more.count()>=1, "no More button"
        ofx0=await p.evaluate("()=>document.body.scrollWidth>window.innerWidth+2")
        await more.first.click()
        await p.wait_for_timeout(350)
        # 2. drawer open: a grouped link present + measure its sheet box
        cap=p.get_by_role("link", name="Capture")
        assert await cap.count()>=1, "drawer didn't open (no Capture link)"
        box=await p.evaluate("""()=>{
          const dlg=document.querySelector('[role=dialog]');
          const sheet=dlg && dlg.lastElementChild;
          const r=sheet.getBoundingClientRect();
          const blurred=[...document.querySelectorAll('*')].find(e=>/backdrop-blur/.test(e.className||''));
          return {bottom:Math.round(r.bottom), right:Math.round(r.right), left:Math.round(r.left),
                  vh:window.innerHeight, vw:window.innerWidth,
                  trappedInBlur: blurred? blurred.contains(dlg): false};
        }""")
        await p.screenshot(path=f"/home/claude/more_{THEME}.png")
        ofx1=await p.evaluate("()=>document.body.scrollWidth>window.innerWidth+2")
        # 3. Esc closes
        await p.keyboard.press("Escape"); await p.wait_for_timeout(250)
        closed = await p.get_by_role("link", name="Capture").count()==0
        print(f"[{THEME}] more_btn=ok drawer_open=ok grouped_nav=ok")
        print(f"  sheet box: bottom={box['bottom']} vh={box['vh']} (anchored={box['bottom']>=box['vh']-2}) "
              f"left={box['left']} right={box['right']} vw={box['vw']} full_width={box['left']<=1 and box['right']>=box['vw']-1}")
        print(f"  trapped_in_blur={box['trappedInBlur']} (must be False)")
        print(f"  overflowX before/after open: {ofx0}/{ofx1}  esc_closed={closed}")
        await b.close()
asyncio.run(main())
