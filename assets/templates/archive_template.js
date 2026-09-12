(function(){
  var root=document.documentElement;
  root.classList.add('js');
  var btn=document.getElementById('themeBtn');
  var animTimer=null;
  btn.addEventListener('click',function(){
    root.classList.add('theme-anim');
    var dark=root.getAttribute('data-theme')==='dark';
    root.setAttribute('data-theme',dark?'light':'dark');
    btn.textContent=dark?'🌙':'☀️';
    clearTimeout(animTimer);
    animTimer=setTimeout(function(){root.classList.remove('theme-anim')},480);
  });
  document.querySelectorAll('a[href^="#"]').forEach(function(a){
    a.addEventListener('click',function(e){
      var id=this.getAttribute('href').slice(1);
      var el=id&&document.getElementById(id);
      if(!el) return;
      e.preventDefault();
      /* 隐藏中的栏（如灾害分析）经导航点击重新调出 */
      if(el.classList.contains('sec-hidden')) el.classList.remove('sec-hidden');
      el.scrollIntoView({behavior:'smooth',block:'start'});
      if(history.replaceState) history.replaceState(null,'', '#'+id);
    });
  });

  /* 章节入场动画（合并卡片内的子区块不单独入场，由所在卡片统一动画） */
  var sections=[].slice.call(document.querySelectorAll('main .section, main .sub-section'));
  if('IntersectionObserver' in window){
    var io=new IntersectionObserver(function(es){
      es.forEach(function(en){
        if(en.isIntersecting){en.target.classList.add('in');io.unobserve(en.target);}
      });
    },{threshold:.06,rootMargin:'0px 0px -30px 0px'});
    sections.forEach(function(s){io.observe(s);});
  }else{
    sections.forEach(function(s){s.classList.add('in');});
  }

  /* 滚动：进度条 / 目录高亮 / 返回顶部 */
  var navLinks=[].slice.call(document.querySelectorAll('.topnav a'));
  var prog=document.getElementById('scrollProgress');
  var backTop=document.getElementById('backTop');
  var ticking=false;
  function onScroll(){
    if(ticking) return; ticking=true;
    requestAnimationFrame(function(){
      ticking=false;
      var st=window.scrollY||document.documentElement.scrollTop;
      var dh=document.documentElement.scrollHeight-window.innerHeight;
      if(prog) prog.style.width=(dh>0?(st/dh*100):0)+'%';
      if(backTop) backTop.classList.toggle('show',st>560);
      var cur='';
      for(var i=0;i<sections.length;i++){
        if(!sections[i].offsetParent) continue; /* 被隐藏的栏不参与高亮 */
        if(sections[i].getBoundingClientRect().top<=80) cur=sections[i].id;
      }
      navLinks.forEach(function(a){
        a.classList.toggle('active',a.getAttribute('href')==='#'+cur);
      });
    });
  }
  window.addEventListener('scroll',onScroll,{passive:true});
  window.addEventListener('resize',onScroll);
  onScroll();
  if(backTop) backTop.addEventListener('click',function(){
    window.scrollTo({top:0,behavior:'smooth'});
  });

  /* 报告双栏切换 */
  function activatePane(pane){
    document.querySelectorAll('.report-pane.active').forEach(function(p){
      if(p!==pane) p.classList.remove('active');
    });
    pane.classList.add('active');
    document.querySelectorAll('.report-entry').forEach(function(e){
      e.classList.toggle('active',e.getAttribute('data-target')===pane.id);
    });
  }
  document.querySelectorAll('.report-entry').forEach(function(e){
    e.addEventListener('click',function(){
      var p=document.getElementById(e.getAttribute('data-target'));
      if(p) activatePane(p);
    });
  });

  /* 物理信息：解锁情报悬浮弹窗 */
  var sizePopup=null,sizePopText=null,popAnchor=null;
  function hideSizePopup(){
    if(sizePopup) sizePopup.classList.remove('open');
    popAnchor=null;
  }
  function ensureSizePopup(){
    if(sizePopup) return;
    sizePopup=document.createElement('div');
    sizePopup.className='size-popup';
    sizePopText=document.createElement('div');
    sizePopup.appendChild(sizePopText);
    document.body.appendChild(sizePopup);
    sizePopup.addEventListener('click',function(e){e.stopPropagation();});
  }
  function showSizePopup(el, text){
    ensureSizePopup();
    if(!text) return;
    if(popAnchor===el&&sizePopup.classList.contains('open')){hideSizePopup();return;}
    popAnchor=el;
    sizePopText.textContent=text;
    sizePopup.classList.add('open');
    var r=el.getBoundingClientRect();
    var pw=sizePopup.offsetWidth,ph=sizePopup.offsetHeight;
    var left=Math.max(8,Math.min(r.left,window.innerWidth-pw-8));
    var top=r.bottom+8;
    if(top+ph>window.innerHeight-8) top=Math.max(8,r.top-ph-8);
    sizePopup.style.left=left+'px';
    sizePopup.style.top=top+'px';
    var arrowX=Math.max(10,Math.min(r.left+r.width/2-left-5,pw-15));
    sizePopup.style.setProperty('--arrow-x',arrowX+'px');
  }
  document.querySelectorAll('#sizes .size-item').forEach(function(b){
    b.addEventListener('click',function(e){
      e.stopPropagation();
      showSizePopup(b,b.getAttribute('data-note'));
    });
  });
  /* 测量报告：独特地标链接 → 悬浮显示当前耐久 */
  document.querySelectorAll('#reports .lm-link').forEach(function(el){
    el.addEventListener('click',function(e){
      e.stopPropagation();
      showSizePopup(el,el.getAttribute('data-lm-info'));
    });
  });
  document.addEventListener('click',hideSizePopup);
  document.addEventListener('keydown',function(e){if(e.key==='Escape') hideSizePopup();});
  window.addEventListener('scroll',hideSizePopup,{passive:true});

  /* 灾害分析栏隐藏按钮：完全隐藏，经顶部导航再次打开 */
  var anaSec=document.getElementById('analysis');
  var anaHide=document.getElementById('anaHideBtn');
  if(anaHide&&anaSec){
    anaHide.addEventListener('click',function(){
      anaSec.classList.add('sec-hidden');
    });
  }

  /* 画廊灯箱 */
  var thumbs=[].slice.call(document.querySelectorAll('#bio .img-wrap img'));
  if(thumbs.length){
    var lb=document.createElement('div');
    lb.className='lightbox';
    lb.innerHTML='<button type="button" class="lb-btn lb-close" title="关闭 (Esc)">✕</button>'+
      '<button type="button" class="lb-btn lb-prev" title="上一张 (←)">‹</button>'+
      '<button type="button" class="lb-btn lb-next" title="下一张 (→)">›</button>'+
      '<figure><img alt=""><figcaption></figcaption></figure>';
    document.body.appendChild(lb);
    var lbImg=lb.querySelector('img'),lbCap=lb.querySelector('figcaption'),lbIdx=0;
    function showLb(i){
      lbIdx=((i%thumbs.length)+thumbs.length)%thumbs.length;
      var t=thumbs[lbIdx];
      lbImg.src=t.src;
      var fig=t.closest('figure');
      var cap=fig?fig.querySelector('figcaption'):null;
      lbCap.textContent=(cap?cap.textContent:'')+'　'+(lbIdx+1)+' / '+thumbs.length;
      lb.classList.add('open');
    }
    function hideLb(){lb.classList.remove('open');}
    thumbs.forEach(function(t,i){
      t.addEventListener('click',function(){showLb(i);});
    });
    lb.addEventListener('click',function(e){
      if(e.target===lb||e.target.tagName==='FIGURE') hideLb();
    });
    lb.querySelector('.lb-close').addEventListener('click',hideLb);
    lb.querySelector('.lb-prev').addEventListener('click',function(){showLb(lbIdx-1);});
    lb.querySelector('.lb-next').addEventListener('click',function(){showLb(lbIdx+1);});
    document.addEventListener('keydown',function(e){
      if(!lb.classList.contains('open')) return;
      if(e.key==='Escape') hideLb();
      else if(e.key==='ArrowLeft') showLb(lbIdx-1);
      else if(e.key==='ArrowRight') showLb(lbIdx+1);
    });
  }

  var input=document.getElementById('archiveSearch');
  var countEl=document.getElementById('searchCount');
  var prevBtn=document.getElementById('searchPrev');
  var nextBtn=document.getElementById('searchNext');
  if(!input) return;
  var hits=[],idx=-1,lastQ='',composing=false,timer=null;
  var SCOPE='#sizes .size-item, #reports .report-text span, #replays .rp-item';

  function clearHits(){
    document.querySelectorAll('mark.search-hit').forEach(function(m){
      var p=m.parentNode;
      p.replaceChild(document.createTextNode(m.textContent),m);
      p.normalize();
    });
    document.querySelectorAll('.has-hit').forEach(function(el){
      el.classList.remove('has-hit');
    });
    document.querySelectorAll('.note-hit').forEach(function(el){
      el.classList.remove('note-hit');
    });
    hits=[];idx=-1;
  }
  function wrapMatches(el,q){
    var qLow=q.toLowerCase();
    var walker=document.createTreeWalker(el,NodeFilter.SHOW_TEXT,null);
    var nodes=[];
    while(walker.nextNode()){
      if(walker.currentNode.nodeValue.toLowerCase().indexOf(qLow)!==-1)
        nodes.push(walker.currentNode);
    }
    nodes.forEach(function(node){
      var text=node.nodeValue,low=text.toLowerCase();
      var frag=document.createDocumentFragment();
      var i=0,j;
      while((j=low.indexOf(qLow,i))!==-1){
        if(j>i) frag.appendChild(document.createTextNode(text.slice(i,j)));
        var mark=document.createElement('mark');
        mark.className='search-hit';
        mark.textContent=text.slice(j,j+q.length);
        frag.appendChild(mark);
        i=j+q.length;
        if(!q.length) break;
      }
      if(i<text.length) frag.appendChild(document.createTextNode(text.slice(i)));
      node.parentNode.replaceChild(frag,node);
    });
  }
  function reveal(el){
    var p=el;
    while(p){
      if(p.tagName==='DETAILS') p.open=true;
      if(p.classList&&p.classList.contains('report-pane')&&!p.classList.contains('active'))
        activatePane(p);
      p=p.parentElement;
    }
  }
  function updateChrome(){
    var n=hits.length,q=input.value.trim();
    var nn=document.querySelectorAll('.note-hit').length;
    var label='';
    if(q) label=n?((idx+1)+'/'+n):(nn?('情报●'+nn):'无匹配');
    countEl.textContent=label;
    prevBtn.disabled=!n;nextBtn.disabled=!n;
  }
  function goTo(i){
    if(!hits.length) return;
    hits.forEach(function(h){h.classList.remove('current')});
    idx=((i%hits.length)+hits.length)%hits.length;
    var cur=hits[idx];
    cur.classList.add('current');
    reveal(cur);
    cur.scrollIntoView({behavior:'smooth',block:'center'});
    updateChrome();
  }
  function runSearch(){
    clearHits();
    var q=input.value.trim();
    lastQ=q;
    if(!q){updateChrome();return;}
    var qLow=q.toLowerCase();
    document.querySelectorAll(SCOPE).forEach(function(el){
      if(el.textContent.toLowerCase().indexOf(qLow)===-1) return;
      wrapMatches(el,q);
      var card=el.closest('.size-item,.record-card,.report-pane');
      if(card) card.classList.add('has-hit');
    });
    /* 解锁情报文本存于 data-note，不在正文中：命中时以圆点提示部位名 */
    document.querySelectorAll('#sizes .size-item').forEach(function(b){
      var note=(b.getAttribute('data-note')||'').toLowerCase();
      if(note&&note.indexOf(qLow)!==-1) b.classList.add('note-hit');
    });
    hits=Array.prototype.slice.call(document.querySelectorAll('mark.search-hit'));
    document.querySelectorAll('.report-pane').forEach(function(p){
      var e=document.querySelector('.report-entry[data-target="'+p.id+'"]');
      if(e) e.classList.toggle('has-hit',!!p.querySelector('mark.search-hit'));
    });
    if(hits.length) goTo(0);
    else updateChrome();
  }
  function schedule(){
    clearTimeout(timer);
    timer=setTimeout(runSearch,120);
  }
  input.addEventListener('compositionstart',function(){composing=true});
  input.addEventListener('compositionend',function(){composing=false;runSearch()});
  input.addEventListener('input',function(){if(!composing) schedule()});
  input.addEventListener('keydown',function(e){
    if(e.key==='Enter'){
      e.preventDefault();
      clearTimeout(timer);
      var q=input.value.trim();
      if(q!==lastQ) runSearch();
      else if(hits.length) goTo(idx+(e.shiftKey?-1:1));
    }else if(e.key==='Escape'){
      input.value='';runSearch();input.blur();
    }
  });
  prevBtn.addEventListener('click',function(){goTo(idx-1)});
  nextBtn.addEventListener('click',function(){goTo(idx+1)});
  document.addEventListener('keydown',function(e){
    if(e.key!=='/'||e.ctrlKey||e.metaKey||e.altKey) return;
    if(document.activeElement===input) return;
    var t=e.target&&e.target.tagName;
    if(t==='INPUT'||t==='TEXTAREA') return;
    e.preventDefault();
    input.focus();input.select();
  });
})();
