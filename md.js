// Small Markdown renderer for Claude replies, so they look the way they do in the terminal:
// **bold** *italic* ~~strike~~ `code` [link](https://...) # headings, lists (nested / numbered), > quotes, tables, ``` code blocks, ---.
// Safe: everything is HTML-escaped first and tags are added afterwards; links must be http(s). Any HTML in a reply is shown as text.
// mdRender(text, link) / mdInline(text, link): link(escaped text) -> HTML with T/P/K refs turned into links.
(function(){
  const esc = s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  function inline(raw, link){
    link = link || (x=>x);
    return String(raw).split(/(`[^`\n]+`)/).map((seg,i)=>{
      if(i%2) return `<code>${esc(seg.slice(1,-1))}</code>`;
      let s = esc(seg)
        .replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g, (m,t,u)=>`<a href="${u}" target="_blank" rel="noopener noreferrer">${t}</a>`)
        .replace(/\*\*([^*\n]+?)\*\*/g, "<b>$1</b>")
        .replace(/__([^_\n]+?)__/g, "<b>$1</b>")
        .replace(/(^|[^*\w])\*([^*\s][^*\n]*?)\*(?!\w)/g, "$1<i>$2</i>")
        .replace(/~~([^~\n]+?)~~/g, "<s>$1</s>");
      return s.replace(/(<a [^>]*>.*?<\/a>)|([^<]+|<[^>]*>)/g, (m,a,txt)=> a ? a : (txt && txt[0]!=="<" ? link(txt) : txt));
    }).join("");
  }
  function cells(l){ return l.trim().replace(/^\||\|$/g,"").split("|").map(c=>c.trim()); }
  function render(raw, link){
    const L = String(raw||"").replace(/\r/g,"").split("\n"); let h = "", i = 0, para = [];
    const flush = ()=>{ if(para.length){ h += `<p>${para.map(x=>inline(x,link)).join("<br>")}</p>`; para = []; } };
    while(i < L.length){
      const l = L[i], t = l.trim();
      if(/^```/.test(t)){                                            
        flush(); const lang = t.slice(3).trim(); const buf = []; i++;
        while(i < L.length && !/^```/.test(L[i].trim())) buf.push(L[i++]);
        i++; h += `<pre${lang?` data-lang="${esc(lang)}"`:""}><code>${esc(buf.join("\n"))}</code></pre>`; continue;
      }
      if(/^\|.*\|\s*$/.test(t) && i+1 < L.length && /^\|?\s*:?-{2,}/.test(L[i+1].trim())){   
        flush(); const head = cells(t); i += 2; const rows = [];
        while(i < L.length && /^\|/.test(L[i].trim())) rows.push(cells(L[i++]));
        h += `<div class="mdtw"><table><thead><tr>${head.map(c=>`<th>${inline(c,link)}</th>`).join("")}</tr></thead><tbody>${rows.map(r=>`<tr>${r.map(c=>`<td>${inline(c,link)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`; continue;
      }
      if(!t){ flush(); i++; continue; }
      let m;
      if((m = t.match(/^(#{1,6})\s+(.*)$/))){ flush(); h += `<div class="mdh h${m[1].length}">${inline(m[2],link)}</div>`; i++; continue; }
      if(/^(-{3,}|\*{3,}|_{3,})$/.test(t)){ flush(); h += "<hr>"; i++; continue; }
      if(/^>\s?/.test(t)){                                           
        flush(); const buf = [];
        while(i < L.length && /^>\s?/.test(L[i].trim())) buf.push(L[i++].trim().replace(/^>\s?/,""));
        h += `<blockquote>${render(buf.join("\n"), link)}</blockquote>`; continue;
      }
      if((m = l.match(/^(\s*)([-*•+]|\d+[.)])\s+(.*)$/))){            
        flush(); let out = "";
        while(i < L.length && (m = L[i].match(/^(\s*)([-*•+]|\d+[.)])\s+(.*)$/))){
          const lvl = Math.min(4, Math.floor(m[1].replace(/\t/g,"  ").length/2)), ol = /\d/.test(m[2]);
          out += `<div class="mdli${ol?" ol":""}" style="margin-left:${lvl*1.3}em"><span class="mdb">${ol?esc(m[2]):"•"}</span><span>${inline(m[3],link)}</span></div>`; i++;
          while(i < L.length && L[i].trim() && /^\s{2,}\S/.test(L[i]) && !/^\s*([-*•+]|\d+[.)])\s+/.test(L[i])){   
            out += `<div class="mdli cont" style="margin-left:${lvl*1.3+1.1}em">${inline(L[i].trim(),link)}</div>`; i++;
          }
        }
        h += `<div class="mdl">${out}</div>`; continue;
      }
      para.push(t); i++;
    }
    flush(); return h;
  }
  window.mdRender = render; window.mdInline = inline;
  document.head.insertAdjacentHTML("beforeend", `<style>
.md{line-height:1.6;overflow-wrap:anywhere}
.md p{margin:.35em 0}
.md b{font-weight:700;color:var(--ink,inherit)}
.md code{font-family:ui-monospace,Consolas,monospace;font-size:.92em;background:var(--lav-soft);color:var(--lav-ink);border-radius:5px;padding:0 4px}
.md pre{background:#1f2330;color:#e6e6e6;border-radius:8px;padding:8px 10px;overflow:auto;font-size:12.5px;line-height:1.45;margin:.5em 0}
.md pre code{background:none;color:inherit;padding:0;font-size:inherit}
.md .mdh{font-weight:700;margin:.7em 0 .25em} .md .mdh.h1{font-size:1.25em} .md .mdh.h2{font-size:1.15em} .md .mdh.h3{font-size:1.05em}
.md .mdl{margin:.3em 0} .md .mdli{display:flex;gap:.45em;margin-top:.15em} .md .mdb{color:var(--muted);flex:none;min-width:.8em}
.md .mdli.cont{display:block;color:inherit}
.md blockquote{margin:.4em 0;padding:.1em .8em;border-left:3px solid var(--line);color:var(--muted)}
.md hr{border:0;border-top:1px solid var(--line);margin:.7em 0}
.md .mdtw{overflow:auto;margin:.5em 0} .md table{border-collapse:collapse;font-size:.92em} .md th,.md td{border:1px solid var(--line);padding:3px 8px;text-align:left;vertical-align:top} .md th{background:var(--hover,rgba(0,0,0,.04))}
.md a{color:var(--blue-ink);text-decoration:underline}
</style>`);
})();
