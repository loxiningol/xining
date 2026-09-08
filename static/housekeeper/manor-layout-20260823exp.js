(function(){
  'use strict';

  var WORLD_W=1920;
  var WORLD_H=1080;
  var STORAGE_KEY='mdq.display.mode.v2';
  var root,viewport,world,drawer,drawerHost,drawerTitle,scrim,switcher;
  var labelLayer=null;
  var buildingLabels={};
  var actorEngine=null;
  var manorReady=false;
  var bootUi=null;
  var revealedBuilding=null;
  var lastBuildingPointer={target:null,type:'',at:0};
  var camera={x:0,y:0,scale:1,min:.36,max:1.42};
  var activeModule=null;
  var activeMarker=null;
  var activeModules=[];
  var activeTrigger=null;
  var pointers=new Map();
  var dragStart=null;
  var pinch=null;
  var suppressClickUntil=0;
  var manorApi={};

  var specs=[
    {id:'furnace1',kind:'furnace',title:'创造熔炉',status:'车道1–2 · 单一系统',x:402,y:320,w:190,h:235,z:40,target:'#atmSectionCreation',focus:'.atm-hub-col[data-hub="a"]',zoneFocus:'manufacture'},
    {id:'furnace2',kind:'furnace',title:'右熔炉（已退役）',status:'hub-b 已退役',x:580,y:320,w:190,h:235,z:41,target:'#atmSectionCreation',focus:'.atm-hub-col[data-hub="b"]',zoneFocus:'manufacture'},
    {id:'quality',kind:'windmill',title:'风车确认队列',status:'等人确认待命',x:977,y:255,w:230,h:285,z:39,target:'#atmSectionReview',focus:'#atmConfirmRounds',zoneFocus:'manufacture'},
    {id:'bakery',kind:'bakery',title:'运行中策略面包房',status:'运行策略读取中',x:1355,y:305,w:260,h:240,z:43,target:'#atmSectionBakery',extras:['#atmSectionStrategies'],focus:'#atmStrategyRoster',zoneFocus:'live'},
    {id:'experimental',kind:'greenhouse',title:'发明口温室',status:'发明口待命',x:520,y:610,w:220,h:215,z:57,target:'#mdqModuleExperimental',zoneFocus:'manufacture'},
    // 仓位工作坊迁到原发明口进度工位；发明口进度楼栋删除
    {id:'pos_greenhouse',kind:'workshop',title:'仓位工作坊',status:'仓位读取中',x:665,y:615,w:160,h:185,z:58,target:'#atmSectionPositions',zoneFocus:'live'},
    {id:'forecast',kind:'board',title:'中央组合告示牌',status:'组合数据读取中',x:960,y:555,w:170,h:135,z:65,target:'#atmSectionForecast',extras:['#atmSectionPeriodSummary']},
    {id:'backtest',kind:'library',title:'溪栖工作室',status:'溪栖工作室待命',x:1215,y:548,w:225,h:205,z:59,target:'#mdqModuleBacktest',extras:['#mdqModuleSincereSpeech']},
    {id:'timeline',kind:'memorial',title:'庄园纪念馆',status:'系统大事年表',x:840,y:865,w:190,h:175,z:79,target:'#mdqModuleTimeline'},
    // 右下角大屋 = 档案室；原仓位棚位留作占位（不进业务面板）
    {id:'strategies',kind:'manor',title:'档案室',status:'未到起始计数时间',x:1388,y:880,w:250,h:190,z:82,target:'#atmSectionArchive',focus:'#atmArchiveHead',zoneFocus:'archive'},
    {id:'pos_slot',kind:'archive',title:'空置棚位',status:'占位',x:435,y:855,w:255,h:230,z:77,placeholder:true},
    {id:'health',kind:'sign',title:'核心服务木牌',status:'服务状态读取中',x:1516,y:502,w:78,h:96,z:72,target:'#mdqModuleHealth'},
    // 三块木牌：左核心服务、中系统操作、右盘面信息（Sep6）
    {id:'actions',kind:'sign',title:'系统操作',status:'系统操作 · 安全网',x:1600,y:528,w:78,h:96,z:73,target:'#mdqModuleOpsSign'},
    {id:'market_sign',kind:'sign',title:'盘面信息木牌',status:'盘面读取中',x:1688,y:554,w:84,h:96,z:74,target:'#mdqModuleMarket'},
    {id:'market',kind:'tower',title:'观风塔',status:'观风塔待命',x:1810,y:500,w:190,h:285,z:70,target:'#atmSectionHoldAssist',zoneFocus:'watch'}
  ];

  var zones=[
    {id:'manufacture',label:'策略制造区',short:'制造区',x:335,y:120,cx:650,cy:330,scale:.82},
    {id:'live',label:'实盘工作区',short:'实盘区',x:1480,y:200,cx:1400,cy:400,scale:.82},
    {id:'archive',label:'研究档案区',short:'档案区',x:1388,y:980,cx:1388,cy:880,scale:.85},
    {id:'watch',label:'观风区',short:'观风区',x:1810,y:380,cx:1810,cy:520,scale:.9}
  ];
  var zoneStatusPanel=null;
  var zoneApiBase='/api/manor/zones';
  var zoneMarkerButtons={};
  var zoneStatusCache={};
  var ZONE_MODULE_FALLBACK={
    live:[
      {title_zh:'运行中策略面包房',manor_building:'bakery'},
      {title_zh:'仓位工作坊',manor_building:'pos_greenhouse'}
    ],
    manufacture:[
      {title_zh:'发明口温室',manor_building:'experimental'},
      {title_zh:'创造熔炉 · 单一系统',manor_building:'furnace1'},
      {title_zh:'右熔炉 · 已退役',manor_building:'furnace2'},
      {title_zh:'风车确认队列',manor_building:'quality'}
    ],
    archive:[{title_zh:'档案室',manor_building:'strategies'}],
    watch:[{title_zh:'观风塔',manor_building:'market'}]
  };

  function depthZ(footY){
    return 1000 + Math.round(footY);
  }
  function hideBroken(img){
    img.addEventListener('error',function(){
      img.dataset.bootBroken='1';
      if(!manorReady)return;
      img.style.visibility='hidden';
      img.removeAttribute('src');
    });
    return img;
  }
  function imageUsable(img){
    return !!(img&&img.complete&&img.naturalWidth>0&&img.naturalHeight>0);
  }
  function canonSrc(value){
    var raw='';
    if(value&&value.getAttribute)raw=String(value.getAttribute('src')||'');
    else raw=String(value||'');
    raw=raw.split('#')[0].split('?')[0].trim();
    if(!raw)return '';
    try{return new URL(raw,document.baseURI||location.href).pathname}
    catch(err){return raw.charAt(0)==='/'?raw:raw}
  }
  function bootSrc(img){
    return canonSrc(img);
  }

  function q(selector,scope){return (scope||document).querySelector(selector)}
  function el(tag,className,text){
    var node=document.createElement(tag);
    if(className)node.className=className;
    if(text!==undefined&&text!==null)node.textContent=text;
    return node;
  }
  function textOf(id){
    var node=document.getElementById(id);
    return node?(node.textContent||'').trim():'';
  }
  function inferState(text){
    text=String(text||'');
    if(/失败|异常|错误|未运行|已停止|停止/.test(text))return 'error';
    if(/正在工作|工作中|研究中|计算中|处理中|读取中|监测中/.test(text))return 'active';
    if(/过期|警告|不足|等待|待/.test(text))return 'warning';
    if(/正常|通过|监测|就绪|完成|空闲/.test(text))return 'ok';
    return 'idle';
  }

  function hideLabelIfIdle(button){
    if(!button)return;
    var label=buildingLabels[button.getAttribute('data-manor-id')];
    if(!label)return;
    if(button.classList.contains('is-label-visible'))return;
    if(document.activeElement===button)return;
    if(button.matches&&button.matches(':hover'))return;
    label.classList.remove('is-visible');
  }

  function showBuildingLabel(button){
    var label=buildingLabels[button.getAttribute('data-manor-id')];
    if(label)label.classList.add('is-visible');
  }

  function clearBuildingReveal(except){
    if(revealedBuilding&&revealedBuilding!==except){
      revealedBuilding.classList.remove('is-label-visible');
      hideLabelIfIdle(revealedBuilding);
    }
    if(!except){
      if(revealedBuilding){
        revealedBuilding.classList.remove('is-label-visible');
        hideLabelIfIdle(revealedBuilding);
      }
      revealedBuilding=null;
    }
  }

  function revealBuilding(button){
    clearBuildingReveal(button);
    revealedBuilding=button;
    button.classList.add('is-label-visible');
    showBuildingLabel(button);
    button.focus({preventScroll:true});
  }

  function isTouchLikeActivation(event,button){
    if(event.detail===0)return false;
    var fresh=lastBuildingPointer.target===button&&Date.now()-lastBuildingPointer.at<1800;
    if(fresh)return lastBuildingPointer.type==='touch'||lastBuildingPointer.type==='pen';
    return !!(window.matchMedia&&window.matchMedia('(hover:none), (pointer:coarse)').matches);
  }

  function setStableModuleIds(){
    var fallbacks=[
      ['#mdqExpSave','mdqModuleBacktest'],
      ['#experimentalStrategies','mdqModuleExperimental'],
      ['#dialysisBox','mdqModuleMarket'],
      ['#processBox','mdqModuleHealth'],
      ['.system-timeline','mdqModuleTimeline'],
      ['button[onclick*="/api/calibrate"]','mdqModuleActions'],
      ['#safetyNetBox','mdqModuleSafetyNet']
    ];
    fallbacks.forEach(function(pair){
      var anchor=q(pair[0]);
      var panel=anchor&&anchor.closest('.panel');
      if(panel&&!panel.id)panel.id=pair[1];
    });
  }

  function bindSwitcher(){
    switcher=document.getElementById('mdqViewSwitch');
    if(!switcher){
      switcher=el('nav','mdq-manor-switch');
      switcher.id='mdqViewSwitch';
      switcher.setAttribute('role','group');
      switcher.setAttribute('aria-label','页面显示方式');
      [['manor','庄园世界'],['classic','经典控制台']].forEach(function(item){
        var button=el('button','',item[1]);
        button.type='button';
        button.dataset.mode=item[0];
        switcher.appendChild(button);
      });
      document.body.appendChild(switcher);
    }
    switcher.querySelectorAll('button[data-mode]').forEach(function(button){
      button.addEventListener('click',function(){setMode(button.dataset.mode,true)});
    });
  }

  function createRouteLayer(){
    var ns='http://www.w3.org/2000/svg';
    var svg=document.createElementNS(ns,'svg');
    svg.setAttribute('class','mdq-manor-routes');
    svg.setAttribute('viewBox','0 0 '+WORLD_W+' '+WORLD_H);
    [
      'M 425 345 C 560 390 735 360 955 300',
      'M 590 345 C 710 400 825 365 955 300',
      'M 980 330 C 1100 360 1220 330 1330 320',
      'M 980 330 C 900 440 790 515 670 610'
    ].forEach(function(d){
      var path=document.createElementNS(ns,'path');
      path.setAttribute('d',d);
      svg.appendChild(path);
    });
    world.appendChild(svg);
  }

  function createZones(){
    zones.forEach(function(zone){
      var button=el('button','mdq-zone-marker',zone.label);
      button.type='button';
      button.dataset.zone=zone.id;
      button.dataset.health='ok';
      button.style.left=zone.x+'px';
      button.style.top=zone.y+'px';
      button.title='查看'+zone.label+'状态';
      button.addEventListener('click',function(){openZoneStatus(zone)});
      var badge=el('i','mdq-zone-badge','');
      badge.hidden=true;
      button.appendChild(badge);
      zoneMarkerButtons[zone.id]=button;
      world.appendChild(button);
    });
  }

  function applyZoneOverview(data){
    var rows=(data&&data.zones)||[];
    rows.forEach(function(row){
      var zone=zones.filter(function(item){return item.id===row.id})[0];
      if(!zone)return;
      if(row.label_zh)zone.label=row.label_zh;
      if(row.short_zh)zone.short=row.short_zh;
      var mk=row.marker||{};
      if(mk.x!=null)zone.x=Number(mk.x);
      if(mk.y!=null)zone.y=Number(mk.y);
      if(mk.cx!=null)zone.cx=Number(mk.cx);
      if(mk.cy!=null)zone.cy=Number(mk.cy);
      if(mk.scale!=null)zone.scale=Number(mk.scale);
      var button=zoneMarkerButtons[zone.id];
      if(!button)return;
      button.style.left=zone.x+'px';
      button.style.top=zone.y+'px';
      button.dataset.health=row.health||'ok';
      button.title='查看'+zone.label+'状态 · '+healthZh(row.health);
      var textNode=null;
      for(var i=0;i<button.childNodes.length;i++){
        if(button.childNodes[i].nodeType===3){textNode=button.childNodes[i];break;}
      }
      if(textNode)textNode.textContent=zone.label;
      else if(!button.querySelector('.mdq-zone-badge'))button.insertBefore(document.createTextNode(zone.label),button.firstChild);
      var badge=button.querySelector('.mdq-zone-badge');
      if(badge){
        var n=Number(row.process_count||0);
        if(n>0){badge.hidden=false;badge.textContent=String(n)}
        else{badge.hidden=true;badge.textContent=''}
      }
    });
  }

  function refreshZoneMarkers(){
    var url=zoneApiBase+'?overview=1';
    var getter=(typeof window.apiGetTimeout==='function')
      ? window.apiGetTimeout(url,8000)
      : fetch(url,{credentials:'same-origin'}).then(function(r){return r.json()});
    Promise.resolve(getter).then(function(data){
      if(data&&data.ok)applyZoneOverview(data);
    }).catch(function(){});
  }

  function createBuilding(spec){
    var button=el('button','mdq-building is-loading');
    button.type='button';
    button.dataset.manorId=spec.id;
    button.dataset.kind=spec.kind;
    button.style.left=spec.x+'px';
    button.style.top=spec.y+'px';
    button.style.setProperty('--w',spec.w+'px');
    button.style.setProperty('--h',spec.h+'px');
    button.style.setProperty('--z',spec.z);
    button.setAttribute('aria-label',spec.title+'：'+spec.status);
    var art=el('span','mdq-building-art');
    var smoke=el('i','mdq-smoke');
    var dot=el('i','mdq-state-dot');
    var label=el('span','mdq-building-label');
    var title=el('span','mdq-building-title',spec.title);
    var status=el('small','mdq-building-status',spec.status);
    art.appendChild(smoke);
    art.appendChild(dot);
    label.appendChild(title);
    label.appendChild(status);
    label.dataset.manorId=spec.id;
    label.dataset.kind=spec.kind;
    label.style.left=spec.x+'px';
    label.style.top=spec.y+'px';
    label.style.setProperty('--w',spec.w+'px');
    label.style.setProperty('--h',spec.h+'px');
    buildingLabels[spec.id]=label;
    if(labelLayer)labelLayer.appendChild(label);
    button.appendChild(art);
    button.addEventListener('pointerenter',function(){showBuildingLabel(button)});
    button.addEventListener('pointerleave',function(){hideLabelIfIdle(button)});
    button.addEventListener('focus',function(){showBuildingLabel(button)});
    button.addEventListener('blur',function(){hideLabelIfIdle(button)});
    button.addEventListener('pointerdown',function(event){
      lastBuildingPointer={target:button,type:event.pointerType||'',at:Date.now()};
    });
    button.addEventListener('touchstart',function(){
      if(lastBuildingPointer.target!==button||Date.now()-lastBuildingPointer.at>500){
        lastBuildingPointer={target:button,type:'touch',at:Date.now()};
      }
    },{passive:true});
    button.addEventListener('click',function(event){
      if(Date.now()<suppressClickUntil)return;
      if(isTouchLikeActivation(event,button)&&revealedBuilding!==button){
        event.preventDefault();
        event.stopPropagation();
        revealBuilding(button);
        return;
      }
      clearBuildingReveal();
      if(spec.placeholder){
        drawerTitle.textContent=spec.title;
        // keep map clickable but no business panel
        if(drawer&&drawerHost){
          restoreModule();
          clearZoneStatusPanel();
          var tip=el('div','atm-section');
          tip.innerHTML='<div class="atm-section-head"><div><h2 class="atm-section-title">'+escapeHtml(spec.title)+'</h2>'+
            '<div class="atm-section-sub">占位棚位 · 仓位工作坊已迁至发明口旁工位</div></div></div>'+
            '<div class="atm-empty">此处仅占位，不进业务面板。</div>';
          drawerHost.appendChild(tip);
          drawer.classList.add('is-open');
          scrim.classList.add('is-open');
          drawer.setAttribute('aria-hidden','false');
          drawer.inert=false;
        }
        return;
      }
      // zoneFocus 只做归属标注；楼栋点击仍下钻业务面板（区域状态靠区标）
      openModule(spec,button);
    });
    if(spec.zoneFocus)button.dataset.zoneFocus=spec.zoneFocus;
    if(spec.placeholder)button.dataset.placeholder='1';
    button.setAttribute('data-interaction-version','two-step-v1');
    world.appendChild(button);
  }

  function createDepthScene(){
    var layer=el('div','mdq-depth-scene');
    function piece(className,x,y,w,h,z){
      var node=el('i',className);
      node.style.left=x+'px';node.style.top=y+'px';
      if(w)node.style.width=w+'px';if(h)node.style.height=h+'px';
      if(z!==undefined)node.style.zIndex=String(z);
      layer.appendChild(node);return node;
    }
    [
      [405,423,185,62],[592,403,178,58],[982,389,215,66],[1350,411,250,72],
      [518,703,235,62],[1218,650,230,64],[455,948,250,67],[839,952,205,55],
      [1385,955,275,78],[1548,930,175,58],[1770,648,175,60]
    ].forEach(function(item){piece('mdq-structure-shadow',item[0],item[1],item[2],item[3],11)});

    [
      ['furnace1',443,350],['furnace2',599,343],['windmill',1015,342],
      ['bakery',1310,350],['manor',1405,900],
      ['pos_greenhouse',486,832],['gazebo',840,900],['workshop',678,568],
      ['actions',1600,528]
    ].forEach(function(item){
      var light=piece('mdq-interaction-light',item[1],item[2],54,38,26);
      light.dataset.scene=item[0];
    });

    world.appendChild(layer);
  }

  var SLICE_ITEMS=[
    {name:'furnace1',layer:'back',file:'manor-slice-furnace1-back-v13.webp',x:310,y:213,w:185,h:136,footY:347},
    {name:'furnace1',layer:'front',file:'manor-slice-furnace1-front-v13.webp',x:313,y:305,w:178,h:58,footY:361},
    {name:'furnace2',layer:'back',file:'manor-slice-furnace2-back-v13.webp',x:490,y:207,w:178,h:128,footY:333},
    {name:'furnace2',layer:'front',file:'manor-slice-furnace2-front-v13.webp',x:491,y:292,w:175,h:59,footY:349},
    {name:'windmill',layer:'back',file:'manor-slice-windmill-back-v5.webp',x:881,y:139,w:215,h:189,footY:313},
    {name:'windmill',layer:'front',file:'manor-slice-windmill-front-v12.webp',x:887,y:261,w:202,h:66,footY:325},
    {name:'bakery',layer:'back',file:'manor-slice-bakery-back-v12.webp',x:1249,y:202,w:235,h:153,footY:353},
    {name:'bakery',layer:'front',file:'manor-slice-bakery-front-v12.webp',x:1253,y:308,w:231,h:59,footY:365},
    {name:'greenhouse',layer:'back',file:'manor-slice-greenhouse-back-v12.webp',x:444,y:534,w:169,h:65,footY:597},
    {name:'workshop',layer:'back',file:'manor-slice-workshop-back-v5.webp',x:595,y:529,w:167,h:132,footY:597},
    {name:'library',layer:'back',file:'manor-slice-library-back-v12.webp',x:1120,y:451,w:217,h:108,footY:557,coverPad:36},
    {name:'library',layer:'front',file:'manor-slice-library-front-v12.webp',x:1122,y:545,w:215,h:68,footY:611},
    {name:'archive',layer:'back',file:'manor-slice-archive-back-v12.webp',x:333,y:748,w:231,h:131,footY:877},
    {name:'gazebo',layer:'back',file:'manor-slice-gazebo-back-v12.webp',x:767,y:798,w:163,h:104,footY:900},
    {name:'gazebo',layer:'front',file:'manor-slice-gazebo-front-v12.webp',x:769,y:869,w:159,h:76,footY:943},
    {name:'manor',layer:'back',file:'manor-slice-manor-back-v12.webp',x:1273,y:705,w:310,h:168,footY:871,coverPad:48},
    {name:'manor',layer:'front',file:'manor-slice-manor-front-v12.webp',x:1278,y:832,w:301,h:57,footY:887},
    {name:'tower',layer:'back',file:'manor-slice-tower-back-v12.webp',x:1724,y:390,w:172,h:179,footY:567},
    {name:'tower',layer:'front',file:'manor-slice-tower-front-v12.webp',x:1729,y:520,w:169,h:63,footY:581}
  ];
  var ROOF_BOXES=[
    {x:309,y:212,w:187,h:118},{x:488,y:205,w:181,h:112},
    {x:881,y:139,w:215,h:155},{x:1248,y:201,w:241,h:132},
    {x:433,y:517,w:191,h:68},{x:606,y:530,w:145,h:52},
    {x:1119,y:450,w:219,h:108},
    {x:1272,y:704,w:312,h:148},{x:332,y:747,w:233,h:108},
    {x:757,y:790,w:183,h:70},{x:1722,y:389,w:185,h:150}
  ];
  function onRoof(x,y){
    for(var i=0;i<ROOF_BOXES.length;i++){
      var b=ROOF_BOXES[i];
      if(x>=b.x&&x<=b.x+b.w&&y>=b.y&&y<=b.y+b.h)return true;
    }
    return false;
  }
  function offRoof(x,y){
    var guard=0;
    while(onRoof(x,y)&&guard++<90)y+=3;
    return [x,y];
  }

  function appendOccluders(layer){
    SLICE_ITEMS.forEach(function(item){
      var mask=el('i','mdq-depth-occluder mdq-depth-slice mdq-depth-'+item.name+' mdq-depth-'+item.layer);
      mask.style.left=item.x+'px';
      mask.style.top=item.y+'px';
      mask.style.width=item.w+'px';
      mask.style.height=item.h+'px';
      mask.style.zIndex=String(depthZ(item.footY+(item.coverPad||0)));
      var img=hideBroken(el('img'));
      img.decoding='async';
      img.loading='eager';
      img.src='/housekeeper/'+item.file;
      img.alt='';
      img.draggable=false;
      mask.appendChild(img);
      mask.dataset.scene=item.name;
      mask.dataset.layer=item.layer;
      layer.appendChild(mask);
    });
  }

  /* Idle mouths are baked cold into world-v7 / furnace slices-v13.
     Live only adds extracted flame + glow + chimney smoke — no cold sticker. */
  var FURNACE_FX=[
    {id:1,live:[430,350,56,47],chimney:[412,220],backFoot:347,frontFoot:361,clear:[394,100,40,120]},
    {id:2,live:[588,321,54,55],chimney:[572,198],backFoot:333,frontFoot:348,clear:[552,74,44,124]}
  ];

  function appendFurnaceFx(layer){
    FURNACE_FX.forEach(function(h){
      var clear=hideBroken(el('img','mdq-chimney-clear mdq-chimney-clear-f'+h.id));
      clear.src='/housekeeper/manor-hearth-chimney-clear-f'+h.id+'-v5.webp';
      clear.alt='';
      clear.draggable=false;
      clear.style.left=h.clear[0]+'px';
      clear.style.top=h.clear[1]+'px';
      clear.style.width=h.clear[2]+'px';
      clear.style.height=h.clear[3]+'px';
      clear.style.zIndex=String(depthZ(h.backFoot)+6);
      layer.appendChild(clear);

      var live=hideBroken(el('img','mdq-hearth-live mdq-hearth-live-f'+h.id));
      live.src='/housekeeper/manor-hearth-live-f'+h.id+'-v5.webp';
      live.alt='';
      live.draggable=false;
      live.style.left=h.live[0]+'px';
      live.style.top=h.live[1]+'px';
      live.style.width=h.live[2]+'px';
      live.style.height=h.live[3]+'px';
      live.style.zIndex=String(depthZ(h.frontFoot)+1);
      layer.appendChild(live);

      var glow=el('i','mdq-hearth-glow mdq-hearth-glow-f'+h.id);
      glow.style.left=(h.live[0]+h.live[2]/2)+'px';
      glow.style.top=(h.live[1]+h.live[3]*0.62)+'px';
      glow.style.width=(h.live[2]+14)+'px';
      glow.style.height=(h.live[3]+10)+'px';
      glow.style.zIndex=String(depthZ(h.frontFoot)+2);
      layer.appendChild(glow);

      var smoke=el('div','mdq-chimney-smoke mdq-chimney-smoke-f'+h.id);
      smoke.style.left=h.chimney[0]+'px';
      smoke.style.top=h.chimney[1]+'px';
      smoke.style.zIndex=String(depthZ(h.backFoot)+8);
      for(var i=0;i<6;i++){
        var puff=el('i');
        puff.style.animationDelay=(i*0.42)+'s';
        smoke.appendChild(puff);
      }
      layer.appendChild(smoke);
    });
  }

  function createManorActorEngine(layer){
    var ASSET='/housekeeper/';
    var GIRL_IDLE=ASSET+'manor-girl-walk-00-v35.webp';
    // Phase A: one standing src + one gait pack only. No life/run/start/stop museum layers.
    // Phase B/D: locked upper through skirt hem; only calves/feet animate; fixed foot crop.
    var POSE={
      idle:GIRL_IDLE,walk:GIRL_IDLE,run:GIRL_IDLE,
      wipe:GIRL_IDLE,clean:GIRL_IDLE,lens:GIRL_IDLE,sleep:GIRL_IDLE,
      carryWood:GIRL_IDLE,stokeFire:GIRL_IDLE,sit:GIRL_IDLE,study:GIRL_IDLE,
      watch:GIRL_IDLE,sweat:GIRL_IDLE,dash:GIRL_IDLE,
      dashRun:GIRL_IDLE
    };
    var WALK_FRAMES=[
      ASSET+'manor-girl-walk-00-v35.webp',
      ASSET+'manor-girl-walk-01-v35.webp',
      ASSET+'manor-girl-walk-02-v35.webp',
      ASSET+'manor-girl-walk-03-v35.webp',
      ASSET+'manor-girl-walk-04-v35.webp',
      ASSET+'manor-girl-walk-05-v35.webp',
      ASSET+'manor-girl-walk-06-v35.webp',
      ASSET+'manor-girl-walk-07-v35.webp'
    ];
    var GIRL_STOP_HOLD_MS=280;
    // Phase C clock: displacement → phase. Target ~8fps walk / ~10fps sprint on same 8-frame pack.
    var GIRL_STRIDE=5;
    var GIRL_WALK=40;
    var GIRL_RUN=80;
    var GIRL_DASH=88;
    var GIRL_WALK_FPS=8;
    var GIRL_SPRINT_FPS=10;
    var DOG_STRIDE=12;
    var DOG_STAND=ASSET+'manor-maltese-stand-v8.webp';
    var DOG_SIT=ASSET+'manor-maltese-idle-v4.webp';
    var BASE_FACING={idle:-1,walk:-1,run:-1,carryWood:-1,clean:-1,lens:-1,stokeFire:-1,wipe:-1,sleep:-1,sit:-1,study:1,watch:1,sweat:1,dash:-1};
    var N={
      plazaW:[805,672],plazaNW:[830,505],plazaN:[930,470],plazaNE:[1060,500],
      plazaE:[1095,570],plazaSE:[1030,640],plazaS:[930,675],plazaSW:[830,678],
      westJ:[735,475],furnaceSE:[705,458],f2gate:[650,448],
      furnaceJ:[638,432],f1a:[478,432],f1work:[496,432],f1watch:[488,438],
      f2a:[618,426],f2work:[658,404],f2watch:[668,462],furnaceMid:[520,428],
      windA:[968,418],windWork:[1018,402],
      bakeryA:[1190,430],bakeryWork:[1318,448],libraryWork:[1208,592],
      east1:[1370,440],east2:[1495,475],boards:[1605,555],tower:[1765,535],
      lowerW1:[755,690],lowerW2:[652,852],archiveA:[540,928],archiveWork:[500,982],
      greenhouseWork:[528,732],optimizerWork:[668,728],
      gazeboWest:[652,940],gazeboSouth:[750,1020],gazeboSE:[925,1010],
      gazeboGate:[890,988],gazeboInside:[870,955],gazeboWork:[850,978],
      gazeboSeat:[838,918],benchDogRest:[786,924],
      manorA:[1024,700],warehouseLane:[1012,812],posStudy:[1605,930],manorWest:[1162,932],manorSW:[1220,960],
      manorSouth:[1330,1005],manorGate:[1405,988],manorDoor:[1440,938],
      manorWork:[1445,948],posGreenhouseWork:[1608,900],holdOrbWork:[1495,870],forecastFront:[950,650]
    };
    var E=[
      ['plazaW','plazaNW'],['plazaNW','plazaN'],['plazaN','plazaNE'],['plazaNE','plazaE'],
      ['plazaE','plazaSE'],['plazaSE','plazaS'],['plazaS','plazaSW'],['plazaSW','plazaW'],
      ['plazaNW','westJ'],['westJ','furnaceSE'],['furnaceSE','f2watch'],['furnaceSE','f2gate'],
      ['f2gate','furnaceJ'],['furnaceJ','f2a'],['f2a','f2work'],
      ['furnaceJ','furnaceMid'],['furnaceMid','f1a'],['f1a','f1work'],['f1a','f1watch'],['furnaceMid','f1watch'],
      ['plazaN','windA'],['windA','windWork'],
      ['plazaNE','bakeryA'],['bakeryA','bakeryWork'],['plazaE','libraryWork'],
      ['bakeryA','east1'],['east1','east2'],['east2','boards'],['boards','tower'],
      ['plazaSW','lowerW1'],['lowerW1','lowerW2'],['lowerW2','archiveA'],['archiveA','archiveWork'],
      ['lowerW1','greenhouseWork'],['lowerW1','optimizerWork'],
      ['lowerW2','gazeboWest'],['gazeboWest','gazeboSouth'],['gazeboSouth','gazeboSE'],
      ['gazeboSE','gazeboGate'],['gazeboGate','gazeboInside'],['gazeboInside','gazeboSeat'],
      ['gazeboInside','benchDogRest'],['gazeboSeat','benchDogRest'],['gazeboGate','gazeboWork'],
      ['plazaSE','manorA'],['manorA','warehouseLane'],['warehouseLane','manorWest'],['manorWest','manorSW'],
      ['warehouseLane','posStudy'],['posStudy','manorWest'],['posStudy','manorDoor'],
      ['posStudy','posGreenhouseWork'],['posGreenhouseWork','manorGate'],
      ['posStudy','holdOrbWork'],['holdOrbWork','manorDoor'],
      ['manorSW','manorSouth'],['manorSouth','manorGate'],['manorGate','manorDoor'],
      ['manorDoor','manorWork'],['plazaS','forecastFront']
    ];
    var ADJ={};
    Object.keys(N).forEach(function(id){ADJ[id]=[]});
    E.forEach(function(edge){
      var a=edge[0],b=edge[1],dx=N[a][0]-N[b][0],dy=N[a][1]-N[b][1];
      var d=Math.hypot(dx,dy);
      ADJ[a].push({id:b,d:d});ADJ[b].push({id:a,d:d});
    });
    if(/\bmanorDebug=1\b/.test(location.search||'')){
      var ns='http://www.w3.org/2000/svg';
      var dbg=document.createElementNS(ns,'svg');
      dbg.setAttribute('class','mdq-walk-debug');
      dbg.setAttribute('viewBox','0 0 '+WORLD_W+' '+WORLD_H);
      E.forEach(function(edge){
        var a=N[edge[0]],b=N[edge[1]],line=document.createElementNS(ns,'line');
        line.setAttribute('x1',a[0]);line.setAttribute('y1',a[1]);
        line.setAttribute('x2',b[0]);line.setAttribute('y2',b[1]);
        dbg.appendChild(line);
      });
      Object.keys(N).forEach(function(id){
        var p=N[id],c=document.createElementNS(ns,'circle'),t=document.createElementNS(ns,'text');
        c.setAttribute('cx',p[0]);c.setAttribute('cy',p[1]);c.setAttribute('r','5');
        t.setAttribute('x',p[0]+7);t.setAttribute('y',p[1]-6);t.textContent=id;
        dbg.appendChild(c);dbg.appendChild(t);
      });
      layer.appendChild(dbg);
    }

    function actor(className,src,x,y){
      var wrap=el('div','mdq-actor '+className);
      var img=hideBroken(el('img','mdq-actor-base'));img.decoding='async';img.loading='eager';img.src=src;img.alt='';img.draggable=false;
      wrap.appendChild(img);
      layer.appendChild(wrap);
      return {el:wrap,img:img,fade:null,dashRun:null,x:x,y:y,px:x,py:y,facing:1,moving:false,path:[],pathIndex:0,
        speed:60,targetNode:null,currentNode:nearestNode(x,y),onArrive:null,holdUntil:0,frames:[],gaitHoldUntil:0};
    }
    var girl=actor('mdq-girl',POSE.idle,N.plazaS[0],N.plazaS[1]);
    var dog=actor('mdq-dog',DOG_STAND,N.plazaSW[0],N.plazaSW[1]);
    var cat=actor('mdq-cat',ASSET+'manor-cat-iso-sleep-v5.webp',N.windWork[0],N.windWork[1]);
    var squirrel=actor('mdq-squirrel',ASSET+'manor-squirrel-iso-v5.webp',N.furnaceJ[0],N.furnaceJ[1]);
    var xiaobai=actor('mdq-xiaobai',ASSET+'manor-xiaobai-idle-v6p.webp',N.plazaS[0]-52,N.plazaS[1]+8);
    var jimao=actor('mdq-jimao',ASSET+'manor-jimao-idle-v6p.webp',N.plazaS[0]-88,N.plazaS[1]+16);
    xiaobai.el.title='小白';jimao.el.title='小鸡毛';
    // 跟随狗（小白狗）按现网约定不显示
    if(dog.el.parentNode)dog.el.parentNode.removeChild(dog.el);
    dog.img.removeAttribute('src');
    dog.hidden=true;
    var rabbit=actor('mdq-rabbit',ASSET+'manor-rabbit-iso-v12.webp',N.greenhouseWork[0],N.greenhouseWork[1]);
    var duck=actor('mdq-duck',ASSET+'manor-duck-iso-v12.webp',N.gazeboSouth[0],N.gazeboSouth[1]);
    var hedgehog=actor('mdq-hedgehog',ASSET+'manor-hedgehog-iso-v12.webp',N.archiveA[0],N.archiveA[1]);
    var owl=actor('mdq-owl',ASSET+'manor-owl-iso-v12.webp',N.libraryWork[0],N.libraryWork[1]);
    rabbit.nextWalk=Date.now()+500;
    duck.nextWalk=Date.now()+1600;
    hedgehog.nextWalk=Date.now()+2700;
    owl.nextWalk=Date.now()+3800;
    function attachFrames(a,className,sources){
      var keepVisible=a===xiaobai||a===jimao;
      var eager=a===dog||a===girl;
      sources.forEach(function(src,index){
        var frame=el('img','mdq-actor-frame '+className+' frame-'+(index+1));
        if(!keepVisible)hideBroken(frame);
        frame.decoding='async';
        if(eager){frame.loading='eager';frame.src=src}
        else frame.dataset.src=src;
        frame.alt='';frame.draggable=false;a.el.appendChild(frame);a.frames.push(frame);
      });
    }
    attachFrames(dog,'mdq-gait-frame',[
      ASSET+'manor-maltese-walk-1-v8.webp',ASSET+'manor-maltese-walk-2-v8.webp',
      ASSET+'manor-maltese-walk-3-v8.webp',ASSET+'manor-maltese-walk-4-v8.webp'
    ]);
    attachFrames(girl,'mdq-gait-frame',WALK_FRAMES);
    girl.gaitFrames=girl.frames.filter(function(img){return img.className.indexOf('mdq-gait-frame')>=0});
    attachFrames(xiaobai,'mdq-gait-frame',[
      ASSET+'manor-xiaobai-walk-1-v6p.webp',ASSET+'manor-xiaobai-walk-2-v6p.webp',
      ASSET+'manor-xiaobai-walk-3-v6p.webp',ASSET+'manor-xiaobai-walk-4-v6p.webp'
    ]);
    attachFrames(jimao,'mdq-gait-frame',[
      ASSET+'manor-jimao-walk-1-v6p.webp',ASSET+'manor-jimao-walk-2-v6p.webp',
      ASSET+'manor-jimao-walk-3-v6p.webp',ASSET+'manor-jimao-walk-4-v6p.webp'
    ]);
    dog.gaitFrames=dog.frames.slice();
    xiaobai.gaitFrames=xiaobai.frames.slice();
    jimao.gaitFrames=jimao.frames.slice();
    xiaobai.nextWalk=Date.now()+800;
    xiaobai.pet='idle';jimao.pet='idle';
    xiaobai.el.dataset.pet='idle';jimao.el.dataset.pet='idle';
    xiaobai.petUntil=0;jimao.petUntil=0;
    xiaobai.petLock=0;jimao.petLock=0;
    xiaobai.nextNuzzle=Date.now()+7000;
    var PET_POSE={
      xiaobai:{
        idle:ASSET+'manor-xiaobai-idle-v6p.webp',
        wave:ASSET+'manor-xiaobai-wave-v6p.webp',
        paw:ASSET+'manor-xiaobai-paw-v6p.webp',
        sit:ASSET+'manor-xiaobai-sit-v6p.webp'
      },
      jimao:{
        idle:ASSET+'manor-jimao-idle-v6p.webp',
        wave:ASSET+'manor-jimao-wave-v6p.webp',
        paw:ASSET+'manor-jimao-paw-v6p.webp',
        sit:ASSET+'manor-jimao-sit-v6p.webp'
      }
    };
    girl.pose='idle';
    girl.el.dataset.pose='idle';
    girl.loco='idle';
    girl.locoT0=Date.now();
    girl.lastWalkIdx=0;
    girl.el.title='管家';
    girl.interactUntil=0;girl.interactLock=0;girl.interactRestore=null;girl.interactResumeMove=false;
    var workSparks=el('div','mdq-work-sparks');layer.appendChild(workSparks);
    var dream=el('div','mdq-actor-dream','Zzz…');layer.appendChild(dream);
    var butterflies=[];
    [0,1,2].forEach(function(index){
      var fly=el('i','mdq-butterfly butterfly-'+(index+1));
      fly.appendChild(el('b','wing left'));fly.appendChild(el('b','wing right'));
      layer.appendChild(fly);butterflies.push({el:fly,t:index*.31,speed:.012+index*.003});
    });
    var butterflyLoops=[
      [[780,650],[865,705],[1015,665],[1080,585]],
      [[610,475],[700,515],[790,485],[710,440]],
      [[1180,815],[1280,900],[1390,915],[1320,805]]
    ];
    var active=false,raf=0,lastTs=0,lastPaint=0,reduced=window.matchMedia&&window.matchMedia('(prefers-reduced-motion:reduce)').matches;
    var busyCreatePipes=[],busyWorkshopPipes=[],pipelineStage='',wasCreateBusy=false,wasWorkshopBusy=false,qualityWakeUntil=0,qualitySignature='';
    var girlQueue=[],girlStep=null,nextIdleAt=0,lastActivity=Date.now(),pipeTurn=0,idleCycleCount=0;
    var girlTrail=[{x:girl.x,y:girl.y}],patrolTargets=['plazaN','greenhouseWork','archiveA','forecastFront','plazaSE','gazeboSouth'];
    var squirrelNext=0,catNext=0,dogResting=false,activeSceneClass='';
    var xiaobaiTrail=[{x:xiaobai.x,y:xiaobai.y}];
    var xiaobaiPatrol=['plazaW','plazaNW','plazaN','plazaNE','plazaE','plazaSE','plazaS','plazaSW','forecastFront','greenhouseWork','gazeboSouth'];

    function nearestNode(x,y){
      var best=null,dist=Infinity;
      Object.keys(N).forEach(function(id){var dx=N[id][0]-x,dy=N[id][1]-y,d=dx*dx+dy*dy;if(d<dist){dist=d;best=id}});
      return best;
    }
    function shortest(from,to){
      if(from===to)return [from];
      var dist={},prev={},open=Object.keys(N);Object.keys(N).forEach(function(id){dist[id]=Infinity});dist[from]=0;
      while(open.length){
        open.sort(function(a,b){return dist[a]-dist[b]});var u=open.shift();if(u===to||!isFinite(dist[u]))break;
        ADJ[u].forEach(function(edge){var alt=dist[u]+edge.d;if(alt<dist[edge.id]){dist[edge.id]=alt;prev[edge.id]=u}});
      }
      var out=[to],cursor=to;while(cursor!==from&&prev[cursor]){cursor=prev[cursor];out.unshift(cursor)}
      return out[0]===from?out:[from,to];
    }
    function catmull(p0,p1,p2,p3,t){
      var t2=t*t,t3=t2*t;
      return [
        .5*((2*p1[0])+(-p0[0]+p2[0])*t+(2*p0[0]-5*p1[0]+4*p2[0]-p3[0])*t2+(-p0[0]+3*p1[0]-3*p2[0]+p3[0])*t3),
        .5*((2*p1[1])+(-p0[1]+p2[1])*t+(2*p0[1]-5*p1[1]+4*p2[1]-p3[1])*t2+(-p0[1]+3*p1[1]-3*p2[1]+p3[1])*t3)
      ];
    }
    function sampledRoute(from,to,start){
      var ids=shortest(from,to),points=ids.map(function(id){return N[id]});
      var out=[];
      if(start&&onRoof(start[0],start[1]))start=offRoof(start[0],start[1]);
      if(start&&Math.hypot(start[0]-points[0][0],start[1]-points[0][1])>4)out.push(start);
      if(points.length===1){out.push(offRoof(points[0][0],points[0][1]));return out}
      for(var i=0;i<points.length-1;i++){
        var p1=points[i],p2=points[i+1];
        var steps=Math.max(2,Math.ceil(Math.hypot(p2[0]-p1[0],p2[1]-p1[1])/12));
        for(var s=0;s<steps;s++){
          var t=s/steps;
          out.push(offRoof(p1[0]+(p2[0]-p1[0])*t,p1[1]+(p2[1]-p1[1])*t));
        }
      }
      out.push(offRoof(points[points.length-1][0],points[points.length-1][1]));return out;
    }
    function visualGirlPose(pose){
      if(pose==='run'||pose==='dash')return 'run';
      if(pose==='walk'||pose==='carryWood')return 'walk';
      return 'idle';
    }
    function girlGaiting(){
      var p=visualGirlPose(girl.pose);
      return !!girl.moving && (p==='walk'||p==='run'||p==='dash');
    }
    function girlSprinting(){
      return !!girl.moving && (girl.pose==='run'||girl.pose==='dash');
    }
    function pinGirlBaseSrc(){
      if(girl.img.getAttribute('src')!==GIRL_IDLE)girl.img.src=GIRL_IDLE;
    }
    function setGirlPose(pose){
      girl.pose=pose;
      girl.el.dataset.pose=pose;
      // Phase A: never swap portrait packs — base stays walk-00.
      pinGirlBaseSrc();
    }
    function faceAlong(a,dx){
      if(Math.abs(dx)>0.45)a.facing=dx>0?1:-1;
    }
    function moveTo(a,node,speed,pose,done){
      a.currentNode=nearestNode(a.x,a.y);a.path=sampledRoute(a.currentNode,node,[a.x,a.y]);a.pathIndex=1;
      a.speed=speed||60;a.targetNode=node;a.onArrive=done||null;a.moving=a.path.length>1;
      a.el.classList.toggle('is-moving',a.moving);
      if(a.path.length>1)faceAlong(a,a.path[1][0]-a.x);
      if(!a.moving&&done)done();
    }
    function advance(a,dt,ts){
      if(!a.moving||!a.path.length)return false;
      var remaining=a.speed*dt,moved=false,travelled=0;
      while(remaining>0&&a.pathIndex<a.path.length){
        var p=a.path[a.pathIndex],dx=p[0]-a.x,dy=p[1]-a.y,d=Math.hypot(dx,dy);
        if(d<=remaining){a.x=p[0];a.y=p[1];a.pathIndex++;remaining-=d;travelled+=d;moved=true}
        else{a.x+=dx/d*remaining;a.y+=dy/d*remaining;travelled+=remaining;remaining=0;moved=true}
        faceAlong(a,dx);
      }
      if(travelled)a.gait=(a.gait||0)+travelled;
      if(a.pathIndex>=a.path.length){
        a.moving=false;a.el.classList.remove('is-moving');a.currentNode=a.targetNode;
        var callback=a.onArrive;a.onArrive=null;if(callback)callback();
      }
      return moved;
    }
    function girlShowGait(a,idx){
      var frames=a.gaitFrames||[];
      var frame=frames[idx];
      if(!imageUsable(frame))return false;
      a.el.classList.add('is-gaiting');
      a.el.setAttribute('data-gait',String(idx));
      // Phase D: no per-frame bob — keeps feet glued to the contact shadow.
      a.el.style.setProperty('--actor-bob','0px');
      girl.lastWalkIdx=idx;
      return true;
    }
    function girlContactIdx(idx){
      // Idle base is walk-00 — always settle on contact 0 (not a mid-stride pass frame).
      var n=Math.max((girl.gaitFrames||[]).length,1);
      var i=((idx%n)+n)%n;
      var d0=Math.min(i,n-i);
      var d4=Math.abs(i-4);
      return d4<d0?4:0;
    }
    function girlHoldGait(a){
      var frames=a.gaitFrames||[];
      var idx=girl.lastWalkIdx||0;
      if(girlShowGait(a,idx))return true;
      // Keep whatever gait attribute is already on — never flash back to base mid-walk.
      if(a.el.classList.contains('is-gaiting')&&a.el.hasAttribute('data-gait'))return true;
      for(var i=0;i<frames.length;i++){
        if(girlShowGait(a,i))return true;
      }
      return false;
    }
    function girlHoldContact(a){
      // Prefer contact matching idle (00); fall back to nearest contact, then hold.
      if(girlShowGait(a,0))return true;
      if(girlShowGait(a,girlContactIdx(girl.lastWalkIdx||0)))return true;
      return girlHoldGait(a);
    }
    function girlClearGait(a){
      a.el.classList.remove('is-gaiting');
      a.el.removeAttribute('data-gait');
      a.el.style.setProperty('--actor-bob','0px');
    }
    function girlStridePx(a,sprint){
      var speed=a.speed||(sprint?GIRL_RUN:GIRL_WALK);
      var fps=sprint?GIRL_SPRINT_FPS:GIRL_WALK_FPS;
      // Cap sprint so it cannot become a 20fps portrait slideshow.
      var stride=speed/Math.max(fps,1);
      if(sprint)return Math.max(GIRL_STRIDE,stride);
      return Math.max(3,stride);
    }
    function applyGait(a,ts){
      var frames=a.gaitFrames||[];
      var walking=frames.length>0&&(a===girl?girlGaiting():!!a.moving);
      if(a===girl){
        var now=Date.now();
        var sprint=girlSprinting();
        if(walking||sprint){
          girl.loco='walk';
          var stride=girlStridePx(a,sprint);
          var framesN=frames.length;
          var idx=Math.floor((a.gait||0)/stride)%Math.max(framesN,1);
          if(!girlShowGait(a,idx))girlHoldGait(a);
          return;
        }
        if(girl.loco==='walk'||girl.loco==='start'||girl.loco==='run'){
          girl.loco='stop';
          girl.locoT0=now;
          // Snap to contact immediately — do not hold a pass/up frame then pop to idle.
          girl.lastWalkIdx=0;
        }
        if(girl.loco==='stop'){
          var stopElapsed=now-girl.locoT0;
          if(stopElapsed<GIRL_STOP_HOLD_MS&&girlHoldContact(a))return;
          girl.loco='idle';
          girl.locoT0=now;
        }
        if(girl.loco!=='idle'){
          girl.loco='idle';
          girl.locoT0=now;
        }
        girlClearGait(a);
        return;
      }
      if(a!==xiaobai&&a!==jimao&&a!==dog)a.el.classList.toggle('is-gaiting',walking);
      if(!frames.length){a.el.removeAttribute('data-gait');return}
      if(a===dog){
        a.el.style.setProperty('--actor-bob','0px');
        if(dogResting){
          a.el.classList.remove('is-gaiting');
          a.el.removeAttribute('data-gait');
          return;
        }
        var now=Date.now();
        if(walking){
          var dogStep=(a.gait||0)/DOG_STRIDE;
          a.el.setAttribute('data-gait',String(Math.floor(dogStep)%Math.max(frames.length,1)));
          a.gaitHoldUntil=now+320;
        }
        var showWalk=walking||now<(a.gaitHoldUntil||0);
        if(!showWalk){
          a.el.classList.remove('is-gaiting');
          a.el.removeAttribute('data-gait');
          return;
        }
        if(!a.el.hasAttribute('data-gait'))a.el.setAttribute('data-gait','0');
        var dogFrame=frames[parseInt(a.el.getAttribute('data-gait'),10)||0];
        if(imageUsable(dogFrame))a.el.classList.add('is-gaiting');
        else a.el.classList.remove('is-gaiting');
        return;
      }
      if(a===xiaobai||a===jimao){
        if(a.pet&&a.pet!=='idle'){
          a.el.classList.remove('is-gaiting');
          a.el.removeAttribute('data-gait');
          a.el.style.setProperty('--actor-bob','0px');
          a.gaitHoldUntil=0;
          return;
        }
        var now=Date.now();
        if(walking)a.gaitHoldUntil=now+220;
        var showWalk=walking||now<(a.gaitHoldUntil||0);
        var stride=20;
        var idx=Math.floor((a.gait||0)/stride)%Math.max(frames.length,1);
        var frame=frames[idx];
        if(!showWalk||!imageUsable(frame)){
          a.el.classList.remove('is-gaiting');
          a.el.removeAttribute('data-gait');
          a.el.style.setProperty('--actor-bob','0px');
          return;
        }
        a.el.classList.add('is-gaiting');
        a.el.setAttribute('data-gait',String(idx));
        a.el.style.setProperty('--actor-bob','0px');
        return;
      }
      if(!walking){a.el.removeAttribute('data-gait');return}
      a.el.setAttribute('data-gait',String(Math.floor((a.gait||0)/8)%frames.length));
    }
    function render(a,ts){
      var walking=a===girl&&girlGaiting();
      var base=1;
      if(a===girl)base=walking?-1:(BASE_FACING[girl.pose]|| -1);
      var flip=a.facing===base?1:-1;
      var foot=a===girl?'-100%':'-88%';
      var left=a.x+'px',top=a.y+'px',z=depthZ(a.y);
      var tf=a===girl?('translate(-50%,'+foot+')'):('translate(-50%,'+foot+') scaleX('+flip+')');
      if(a===girl){
        a.el.style.setProperty('--girl-flip',String(flip));
        a.el.setAttribute('data-iso',flip<0?'se':'sw');
      }
      if(a._left!==left){a.el.style.left=left;a._left=left}
      if(a._top!==top){a.el.style.top=top;a._top=top}
      if(a._z!==z){a.el.style.zIndex=String(z);a._z=z}
      if(a._tf!==tf){a.el.style.transform=tf;a._tf=tf}
      applyGait(a,ts);
    }
    function queueHold(pose,ms,atNode,sceneClass){girlQueue.push({type:'hold',pose:pose,ms:ms,node:atNode||null,sceneClass:sceneClass||''})}
    function queueMove(node,pose,speed){girlQueue.push({type:'move',node:node,pose:pose||'walk',speed:speed||GIRL_WALK})}
    function randMs(lo,hi){return lo+Math.random()*(hi-lo)}
    function pipeNum(pipe){
      return Number(pipe&&(pipe.pipeline||pipe.pipeline_id)||0)||0;
    }
    function createBusy(){return busyCreatePipes.length>0}
    function workshopBusy(){return busyWorkshopPipes.length>0}
    function girlWorkFamily(){
      var c=createBusy(),w=workshopBusy();
      var q=c&&/quality|blind_oos|handoff/.test(pipelineStage);
      if(q&&w)return 'mixed';
      if(q)return 'quality';
      if(c&&w)return 'mixed';
      if(c)return 'create';
      if(w)return 'workshop';
      return '';
    }
    function interruptGirl(){
      girlQueue=[];girlStep=null;girl.moving=false;girl.path=[];girl.onArrive=null;
      girl.el.classList.remove('is-moving');
      clearScene();
    }
    function maybeInterruptWork(prevFamily){
      var next=girlWorkFamily();
      if(!next)return;
      if(prevFamily!==next)interruptGirl();
    }
    function markWorkActivity(){
      if(createBusy()||workshopBusy()){lastActivity=Date.now();nextIdleAt=0}
      if(createBusy())wasCreateBusy=true;
      else if(workshopBusy())wasCreateBusy=false;
      if(workshopBusy())wasWorkshopBusy=true;
      else if(createBusy())wasWorkshopBusy=false;
    }
    function buildWorkCycle(){
      if(!busyCreatePipes.length)return;
      var pipe=busyCreatePipes[pipeTurn++%busyCreatePipes.length],prefix=pipe===2?'f2':'f1';
      var work=prefix+'work',watch=prefix+'watch';
      var roll=Math.random();
      if(roll<0.48){
        queueMove(watch,'walk',GIRL_WALK);
        queueHold('watch',randMs(5000,10000),watch);
      }else if(roll<0.82){
        queueMove(prefix+'a','walk',GIRL_WALK);
        queueMove(work,'carryWood',28);
        queueHold('stokeFire',randMs(2400,4200),work);
        queueMove(watch,'walk',GIRL_WALK);
        queueHold('watch',randMs(3500,7000),watch);
      }else{
        queueMove(watch,'walk',GIRL_WALK);
        queueHold('watch',randMs(6000,11000),watch);
      }
    }
    function buildWorkshopCycle(){
      if(!busyWorkshopPipes.length)return;
      var roll=Math.random();
      if(roll<0.5){
        queueMove('optimizerWork','walk',GIRL_WALK);
        queueHold('watch',randMs(4000,9000),'optimizerWork');
      }else if(roll<0.78){
        queueMove('optimizerWork','walk',GIRL_WALK);
        queueHold('wipe',randMs(1800,3200),'optimizerWork');
      }else{
        queueMove('optimizerWork','walk',GIRL_WALK);
        queueHold('sweat',randMs(2500,4500),'optimizerWork');
      }
    }
    function buildWorkshopWrapCycle(){
      queueMove('optimizerWork','walk',GIRL_WALK);
      queueHold('wipe',1600,'optimizerWork');
    }
    function buildQualityCycle(){
      queueMove('windWork','walk',GIRL_WALK);queueHold('watch',randMs(2800,4800),'windWork','has-windmill-check');
      if(/handoff|done|pass/.test(pipelineStage)){queueMove('bakeryWork','walk',GIRL_WALK);queueHold('wipe',1400,'bakeryWork','has-bakery-delivery')}
    }
    function buildCompletionCycle(){
      queueMove('windWork','walk',GIRL_WALK);queueHold('wipe',1200,'windWork','has-windmill-check');
      queueMove('bakeryWork','walk',GIRL_WALK);queueHold('idle',2200,'bakeryWork','has-bakery-delivery');
    }
    function hasOpenPositions(){
      if(root.classList.contains('has-open-positions'))return true;
      var t=textOf('atmPositionCount')||'';
      var m=String(t).match(/(\d+)\s*个仓位/);
      return !!(m&&parseInt(m[1],10)>0);
    }
    function buildPositionStudyCycle(){
      queueMove('posStudy','walk',GIRL_WALK);
      queueHold('study',randMs(18000,32000),'posStudy','has-position-study');
    }
    function buildIdleCycle(now){
      idleCycleCount++;
      if(hasOpenPositions()){
        buildPositionStudyCycle();
        return;
      }
      if(now-lastActivity>90000||idleCycleCount%5===0){
        if(girlAlreadyAt('gazeboSeat')){
          var leave=patrolTargets[Math.floor(Math.random()*patrolTargets.length)]||'plazaS';
          queueMove(leave,'walk',GIRL_WALK);queueHold('idle',randMs(8000,14000));
          return;
        }
        queueMove('gazeboSeat','walk',GIRL_WALK);queueHold('sit',randMs(14000,24000),'gazeboSeat','has-bench-rest');return;
      }
      if(idleCycleCount%9===0){
        queueMove('archiveWork','walk',GIRL_WALK);queueHold('clean',3800,'archiveWork','has-archive-delivery');return;
      }
      if(idleCycleCount%11===0){
        queueMove('manorWork','walk',GIRL_WALK);queueHold('wipe',3800,'manorWork','has-live-delivery');return;
      }
      if(Math.random()<0.68){
        queueHold('idle',randMs(9000,18000));
        return;
      }
      var target=patrolTargets[Math.floor(Math.random()*patrolTargets.length)];
      queueMove(target,'walk',GIRL_WALK);queueHold('idle',randMs(8000,14000));
    }
    function clearScene(){
      if(activeSceneClass)root.classList.remove(activeSceneClass);
      activeSceneClass='';root.classList.remove('has-stoke-fire');
    }
    function girlAlreadyAt(node){
      var p=N[node];
      return !!(p&&Math.hypot(girl.x-p[0],girl.y-p[1])<8);
    }
    function beginGirlStep(now,ts){
      while(!girlStep&&girlQueue.length){
        girlStep=girlQueue.shift();
        if(girlStep.type==='move'){
          if(girlAlreadyAt(girlStep.node)){
            girl.currentNode=girlStep.node;
            girl.moving=false;
            girl.el.classList.remove('is-moving');
            girlStep=null;
            continue;
          }
          var travel=girlStep.pose||'walk';
          if(travel!=='walk'&&travel!=='run'&&travel!=='dash'&&travel!=='carryWood')travel='walk';
          setGirlPose(travel);
          moveTo(girl,girlStep.node,girlStep.speed,girlStep.pose,function(){girlStep=null});
          return;
        }
        setGirlPose(girlStep.pose);girlStep.until=now+girlStep.ms;
        girl.moving=false;girl.el.classList.remove('is-moving');
        if(/^(f1watch|f1work|f2watch|f2work|windWork|optimizerWork)$/.test(girlStep.node||'')||girlStep.pose==='watch'||girlStep.pose==='sweat'||girlStep.pose==='stokeFire'){
          girl.facing=-1;
        }
        if(girlStep.pose==='study')girl.facing=1;
        clearScene();
        if(girlStep.sceneClass){activeSceneClass=girlStep.sceneClass;root.classList.add(activeSceneClass)}
        if(girlStep.pose==='stokeFire'){
          root.classList.add('has-stoke-fire');
          var sparkAt={f1work:[452,382],f2work:[616,353]}[girlStep.node]||[girl.x,girl.y-34];
          var sparkFoot={f1work:361,f2work:348}[girlStep.node]||girl.y;
          workSparks.style.left=sparkAt[0]+'px';
          workSparks.style.top=sparkAt[1]+'px';
          workSparks.style.zIndex=String(depthZ(sparkFoot)+3);
        }
        return;
      }
    }
    function girlInteracting(){return Date.now()<(girl.interactUntil||0)}
    function startGirlReact(pose,ms,poked){
      var now=Date.now();
      if(!girl.interactRestore)girl.interactRestore=girl.pose;
      girl.interactResumeMove=girl.interactResumeMove||!!girl.moving;
      girl.moving=false;
      girl.el.classList.remove('is-moving','is-gaiting');
      girl.el.removeAttribute('data-gait');
      girl.loco='idle';
      girl.locoT0=now;
      setGirlPose(pose);
      girl.interactUntil=Math.max(girl.interactUntil||0,now+ms);
      if(girlStep&&girlStep.type==='hold')girlStep.until=Math.max(girlStep.until||0,now+ms);
      if(poked){
        girl.el.classList.remove('is-poked');
        void girl.el.offsetWidth;
        girl.el.classList.add('is-poked');
      }
    }
    function finishGirlInteract(){
      girl.el.classList.remove('is-poked');
      if(!girl.el.classList.contains('is-watching'))girl.el.style.setProperty('--pet-tilt','0deg');
      var back=girl.interactRestore;
      girl.interactRestore=null;
      girl.interactUntil=0;
      if(girlStep&&girlStep.type==='hold')setGirlPose(girlStep.pose);
      else if(girl.interactResumeMove&&girl.path&&girl.pathIndex<girl.path.length){
        girl.moving=true;
        girl.el.classList.add('is-moving');
        var travel=(girlStep&&girlStep.pose)||back||'walk';
        if(travel!=='walk'&&travel!=='run'&&travel!=='dash'&&travel!=='carryWood')travel='walk';
        setGirlPose(travel);
      }else if(back&&back!=='walk'&&back!=='run'&&back!=='dash')setGirlPose(back);
      else setGirlPose('idle');
      girl.interactResumeMove=false;
    }
    function pokeGirl(){
      var now=Date.now();
      if(now<(girl.interactLock||0))return;
      girl.interactLock=now+650;
      girl.el.classList.remove('is-poked');
      void girl.el.offsetWidth;
      girl.el.classList.add('is-poked');
      spawnHeart(girl);
      setTimeout(function(){girl.el.classList.remove('is-poked')},420);
    }
    function bindGirl(){
      girl.el.addEventListener('pointerdown',function(ev){
        ev.stopPropagation();
        ev.stopImmediatePropagation();
        ev.preventDefault();
        var r=girl.el.getBoundingClientRect();
        faceAlong(girl,ev.clientX-(r.left+r.width/2));
        pokeGirl();
      });
      girl.el.addEventListener('pointerenter',function(){
        girl.el.classList.add('is-watching');
      });
      girl.el.addEventListener('pointerleave',function(){
        girl.el.classList.remove('is-watching');
        if(!girlInteracting())girl.el.style.setProperty('--pet-tilt','0deg');
      });
      girl.el.addEventListener('pointermove',function(ev){
        var r=girl.el.getBoundingClientRect();
        var dx=(ev.clientX-(r.left+r.width/2))/Math.max(r.width,1);
        girl.el.style.setProperty('--pet-tilt',(Math.max(-1,Math.min(1,dx))*9).toFixed(1)+'deg');
      });
    }
    function updateGirl(now,dt,ts){
      if(girl.interactUntil&&now>=girl.interactUntil)finishGirlInteract();
      if(girlInteracting()){
        dream.style.opacity=girl.pose==='sleep'?'1':'0';dream.style.left=(girl.x+28)+'px';dream.style.top=(girl.y-112)+'px';
        return;
      }
      var moved=advance(girl,dt,ts);
      if(moved){
        var last=girlTrail[girlTrail.length-1];
        if(!last||Math.hypot(girl.x-last.x,girl.y-last.y)>3)girlTrail.push({x:girl.x,y:girl.y});
        if(girlTrail.length>520){
          var drop=girlTrail.length-420;
          girlTrail.splice(0,drop);
          if(dog.trailI!=null)dog.trailI=Math.max(0,dog.trailI-drop);
        }
      }
      if(girlStep&&girlStep.type==='hold'&&now>=girlStep.until){
        var ended=girlStep.pose;
        clearScene();girlStep=null;
        if(!createBusy()&&!workshopBusy()&&!hasOpenPositions()){
          nextIdleAt=now+randMs(6000,11000);
          if(ended!=='sit'&&ended!=='study')setGirlPose('idle');
        }
      }
      if(!girlStep&&!girlQueue.length){
        var quality=createBusy()&&/quality|blind_oos|handoff/.test(pipelineStage);
        var creating=createBusy(),shop=workshopBusy();
        if(quality&&shop){if(pipeTurn++%2===0)buildQualityCycle();else buildWorkshopCycle()}
        else if(quality)buildQualityCycle();
        else if(creating&&shop){if(pipeTurn++%2===0)buildWorkCycle();else buildWorkshopCycle()}
        else if(creating)buildWorkCycle();
        else if(shop)buildWorkshopCycle();
        else if(wasCreateBusy){wasCreateBusy=false;wasWorkshopBusy=false;buildCompletionCycle()}
        else if(wasWorkshopBusy){wasWorkshopBusy=false;buildWorkshopWrapCycle()}
        else if(hasOpenPositions()&&now>=nextIdleAt){buildPositionStudyCycle();nextIdleAt=now+4000}
        else if(now>=nextIdleAt){buildIdleCycle(now);nextIdleAt=now+7000}
      }
      beginGirlStep(now,ts);
      dream.style.opacity=girl.pose==='sleep'?'1':'0';dream.style.left=(girl.x+28)+'px';dream.style.top=(girl.y-112)+'px';
    }
    function girlTrailBehind(distance){
      if(girlTrail.length<2)return {i:0,p:{x:girl.x-65,y:girl.y+10}};
      var remain=distance;
      for(var i=girlTrail.length-1;i>0;i--){
        var a=girlTrail[i],b=girlTrail[i-1],d=Math.hypot(a.x-b.x,a.y-b.y);
        if(d>=remain){
          var t=remain/Math.max(d,.001);
          return {i:i-1,p:{x:a.x+(b.x-a.x)*t,y:a.y+(b.y-a.y)*t}};
        }
        remain-=d;
      }
      return {i:0,p:girlTrail[0]};
    }
    function nearestTrailIndex(x,y){
      var best=0,bestD=1e9;
      for(var i=0;i<girlTrail.length;i++){
        var p=girlTrail[i],d=Math.hypot(p.x-x,p.y-y);
        if(d<bestD){bestD=d;best=i}
      }
      return best;
    }
    function dogTrailWaypoint(follow){
      if(!girlTrail.length)return follow.p;
      if(dog.trailI==null)dog.trailI=nearestTrailIndex(dog.x,dog.y);
      dog.trailI=Math.max(0,Math.min(girlTrail.length-1,dog.trailI));
      var p=girlTrail[dog.trailI];
      var here=p&&Math.hypot(dog.x-p.x,dog.y-p.y)<6;
      if(dog.trailI<follow.i){
        if(here)dog.trailI++;
        return girlTrail[dog.trailI]||follow.p;
      }
      if(dog.trailI>follow.i){
        if(here)dog.trailI--;
        return girlTrail[dog.trailI]||follow.p;
      }
      return follow.p;
    }
    function updateDog(ts,dt){
      if(dog.hidden || !dog.el.parentNode)return;
      if(girl.pose==='sit'){
        if(!dogResting){
          dogResting=true;dog.trailI=null;
          if(dog.img.getAttribute('src')!==DOG_SIT)dog.img.src=DOG_SIT;
          moveTo(dog,'benchDogRest',52,null);
        }
        advance(dog,dt,ts);render(dog,ts);return;
      }
      if(dogResting){
        dogResting=false;dog.moving=false;dog.path=[];dog.onArrive=null;dog.el.classList.remove('is-moving');
        dog.trailI=null;
        if(dog.img.getAttribute('src')!==DOG_STAND)dog.img.src=DOG_STAND;
      }
      var follow=girlTrailBehind(124);
      follow.p={x:follow.p.x, y:follow.p.y+10};
      var hold=dog.moving?18:34;
      var budget=34*dt,travelled=0,hops=0;
      while(budget>0.15&&hops++<24){
        var dFollow=Math.hypot(follow.p.x-dog.x,follow.p.y-dog.y);
        if(dFollow<=hold)break;
        var wp=dogTrailWaypoint(follow);
        var dx=wp.x-dog.x,dy=wp.y-dog.y,d=Math.hypot(dx,dy);
        if(d<0.4){
          if(dog.trailI==null)break;
          if(dog.trailI<follow.i)dog.trailI++;
          else if(dog.trailI>follow.i)dog.trailI--;
          else break;
          continue;
        }
        var step=Math.min(budget,d,Math.max(0,dFollow-10));
        if(step<0.15)break;
        dog.x+=dx/d*step;dog.y+=dy/d*step;
        budget-=step;travelled+=step;
        if(Math.abs(dx)>1.8)dog.facing=dx>0?1:-1;
      }
      if(travelled>=0.5)dog.gait=(dog.gait||0)+travelled;
      if(travelled>0.7)dog.moving=true;
      else if(travelled<0.25)dog.moving=false;
      dog.el.classList.toggle('is-moving',!!dog.moving);
      render(dog,ts);
    }
    function petKey(a){return a===jimao?'jimao':'xiaobai'}
    function petBusy(a){return !!(a.pet&&a.pet!=='idle'&&Date.now()<a.petUntil)}
    function setPet(a,act,ms){
      a.pet=act||'idle';
      a.petUntil=Date.now()+(ms||0);
      a.el.dataset.pet=a.pet;
      var src=PET_POSE[petKey(a)][a.pet]||PET_POSE[petKey(a)].idle;
      if(a.img.getAttribute('src')!==src)a.img.src=src;
      if(a.pet==='idle'){
        a.el.classList.remove('is-poked','is-nuzzle','is-dance');
        a.el.style.setProperty('--pet-tilt','0deg');
        a.sitSide=0;
      }else{
        a.moving=false;a.path=[];a.onArrive=null;
        a.el.classList.remove('is-moving','is-gaiting');
      }
    }
    function clearPetIfDue(a,now){
      if(a.pet&&a.pet!=='idle'&&now>=a.petUntil)setPet(a,'idle',0);
    }
    function spawnHeart(a){
      var h=el('i','mdq-pet-heart');
      a.el.appendChild(h);
      setTimeout(function(){if(h.parentNode)h.parentNode.removeChild(h)},920);
    }
    function pokePet(a){
      var now=Date.now();
      if(now<a.petLock)return;
      a.petLock=now+650;
      var act=a===jimao?(Math.random()<.55?'paw':'wave'):(Math.random()<.55?'wave':'paw');
      setPet(a,act,1500);
      a.el.classList.add('is-poked');
      spawnHeart(a);
      a.nextWalk=now+1700;
      var other=a===xiaobai?jimao:xiaobai;
      if(Math.hypot(other.x-a.x,other.y-a.y)<72){
        setPet(other,other===jimao?'paw':'wave',1500);
        other.el.classList.add('is-poked','is-nuzzle');
        a.el.classList.add('is-nuzzle');
        spawnHeart(other);
        faceEachOther(a,other);
      }
    }
    function petArrive(a){
      var roll=Math.random(),act='idle',ms=1800+Math.random()*2400;
      if(roll<0.34){act='sit';ms=3400+Math.random()*2600}
      else if(roll<0.54){act='wave';ms=1400+Math.random()*800}
      else if(roll<0.68){act=a===jimao?'paw':'wave';ms=1300+Math.random()*700}
      setPet(a,act,ms);
      a.nextWalk=Date.now()+ms+400;
    }
    function choosePetPatrol(a,targets,speed,now){
      if(petBusy(a)||a.moving)return;
      if(now<(a.nextWalk||0))return;
      setPet(a,'idle',0);
      var target=targets[Math.floor(Math.random()*targets.length)];
      moveTo(a,target,speed,null,function(){petArrive(a)});
    }
    function faceEachOther(left,right){
      if(right.x>left.x){left.facing=1;right.facing=-1}else{left.facing=-1;right.facing=1}
    }
    var PET_SIT_GAP_X=76,PET_SIT_GAP_Y=18;
    function sitBesideTarget(anchor,other){
      var side=anchor.sitSide;
      if(side!==1&&side!==-1){
        side=other.x<anchor.x?-1:1;
        if(Math.abs(other.x-anchor.x)<20)side=anchor.facing>=0?-1:1;
        anchor.sitSide=side;
      }
      return {x:anchor.x+side*PET_SIT_GAP_X,y:anchor.y+(side>0?PET_SIT_GAP_Y:-PET_SIT_GAP_Y)};
    }
    function spreadSitPair(){
      var dx=jimao.x-xiaobai.x;
      var midX=(xiaobai.x+jimao.x)/2,midY=(xiaobai.y+jimao.y)/2;
      var jimaoRight=dx>=0;
      if(Math.abs(dx)<12)jimaoRight=xiaobai.facing>0;
      var hx=PET_SIT_GAP_X/2,hy=PET_SIT_GAP_Y/2;
      if(jimaoRight){
        xiaobai.x=midX-hx;jimao.x=midX+hx;
        xiaobai.y=midY-hy;jimao.y=midY+hy;
      }else{
        jimao.x=midX-hx;xiaobai.x=midX+hx;
        jimao.y=midY-hy;xiaobai.y=midY+hy;
      }
      faceEachOther(xiaobai,jimao);
      xiaobai.sitSide=jimao.x<xiaobai.x?-1:1;
    }
    function sitPairTooClose(){
      return Math.abs(jimao.x-xiaobai.x)<68&&Math.abs(jimao.y-xiaobai.y)<36;
    }
    function maybeDuo(now){
      if(now<(xiaobai.nextNuzzle||0))return;
      if(petBusy(xiaobai)||petBusy(jimao)||xiaobai.moving||jimao.moving)return;
      if(Math.hypot(jimao.x-xiaobai.x,jimao.y-xiaobai.y)>48)return;
      xiaobai.nextNuzzle=now+8000+Math.random()*9000;
      xiaobai.nextWalk=now+3200;
      faceEachOther(xiaobai,jimao);
      if(Math.random()<0.42){
        setPet(xiaobai,'wave',2200);
        setPet(jimao,'paw',2200);
        xiaobai.el.classList.add('is-dance');
        jimao.el.classList.add('is-dance');
        spawnHeart(xiaobai);spawnHeart(jimao);
      }else{
        spreadSitPair();
        setPet(xiaobai,'sit',2800);
        setPet(jimao,'sit',2800);
        xiaobai.el.classList.add('is-nuzzle');
        jimao.el.classList.add('is-nuzzle');
        spawnHeart(xiaobai);
      }
    }
    function bindPets(){
      [xiaobai,jimao].forEach(function(a){
        a.el.addEventListener('pointerdown',function(ev){
          ev.stopPropagation();
          ev.stopImmediatePropagation();
          ev.preventDefault();
          pokePet(a);
        });
        a.el.addEventListener('pointerenter',function(ev){
          a.el.classList.add('is-watching');
          if(ev.pointerType==='mouse'&&!petBusy(a)&&a.pet==='idle'&&!a.moving&&Math.random()<0.4){
            setPet(a,a===jimao?'paw':'wave',900);
          }
        });
        a.el.addEventListener('pointerleave',function(){
          a.el.classList.remove('is-watching');
          if(!petBusy(a))a.el.style.setProperty('--pet-tilt','0deg');
        });
        a.el.addEventListener('pointermove',function(ev){
          var r=a.el.getBoundingClientRect();
          var dx=(ev.clientX-(r.left+r.width/2))/Math.max(r.width,1);
          a.el.style.setProperty('--pet-tilt',(Math.max(-1,Math.min(1,dx))*9).toFixed(1)+'deg');
        });
      });
    }
    function choosePatrol(a,targets,speed,now,waitProp){
      if(a.moving)return;
      if(now<(a[waitProp]||0))return;
      var target=targets[Math.floor(Math.random()*targets.length)];
      moveTo(a,target,speed,null,function(){a[waitProp]=Date.now()+2500+Math.random()*4500});
    }
    function trailBehind(trail,fallback,distance){
      if(!trail||trail.length<2)return {x:fallback.x-40,y:fallback.y+8};
      var remain=distance;
      for(var i=trail.length-1;i>0;i--){
        var a=trail[i],b=trail[i-1],d=Math.hypot(a.x-b.x,a.y-b.y);
        if(d>=remain){var t=remain/Math.max(d,.001);return {x:a.x+(b.x-a.x)*t,y:a.y+(b.y-a.y)*t}}
        remain-=d;
      }
      return trail[0];
    }
    function followActor(follower,target,speed,dt){
      var dx=target.x-follower.x,dy=target.y-follower.y,d=Math.hypot(dx,dy);
      var hold=follower.moving?18:38;
      if(d>hold){
        var step=Math.min(Math.max(d-14,0),speed*dt);
        if(step>0.4){
          follower.x+=dx/d*step;follower.y+=dy/d*step;follower.gait=(follower.gait||0)+step;
          if(Math.abs(dx)>5)follower.facing=dx>0?1:-1;
          follower.moving=true;
        }else{
          follower.moving=false;
        }
      }else{
        follower.moving=false;
      }
      follower.el.classList.toggle('is-moving',follower.moving);
    }
    function updateXiaobai(now,dt,ts){
      clearPetIfDue(xiaobai,now);
      maybeDuo(now);
      choosePetPatrol(xiaobai,xiaobaiPatrol,48,now);
      var moved=advance(xiaobai,dt,ts);
      if(moved){
        var last=xiaobaiTrail[xiaobaiTrail.length-1];
        if(!last||Math.hypot(xiaobai.x-last.x,xiaobai.y-last.y)>3)xiaobaiTrail.push({x:xiaobai.x,y:xiaobai.y});
        if(xiaobaiTrail.length>360)xiaobaiTrail.splice(0,xiaobaiTrail.length-280);
      }
      render(xiaobai,ts);
    }
    function updateJimao(now,dt,ts){
      clearPetIfDue(jimao,now);
      if(xiaobai.pet==='sit'&&jimao.pet==='sit'&&sitPairTooClose())spreadSitPair();
      if(petBusy(jimao)){
        jimao.moving=false;
        jimao.el.classList.remove('is-moving','is-gaiting');
        render(jimao,ts);
        return;
      }
      if(xiaobai.pet==='sit'){
        var meet=sitBesideTarget(xiaobai,jimao);
        var d=Math.hypot(meet.x-jimao.x,meet.y-jimao.y);
        if(d>10){
          var step=Math.min(d,54*dt);
          jimao.x+=(meet.x-jimao.x)/d*step;
          jimao.y+=(meet.y-jimao.y)/d*step;
          jimao.gait=(jimao.gait||0)+step;
          if(Math.abs(meet.x-jimao.x)>5)jimao.facing=meet.x>jimao.x?1:-1;
          jimao.moving=true;
          jimao.el.classList.add('is-moving');
        }else{
          jimao.x=meet.x;jimao.y=meet.y;
          jimao.moving=false;
          jimao.el.classList.remove('is-moving','is-gaiting');
          faceEachOther(xiaobai,jimao);
          var hold=Math.max(600,(xiaobai.petUntil||now)-now);
          if(jimao.pet!=='sit')setPet(jimao,'sit',hold);
          jimao.el.classList.add('is-nuzzle');
          xiaobai.el.classList.add('is-nuzzle');
        }
        render(jimao,ts);
        return;
      }
      followActor(jimao,trailBehind(xiaobaiTrail,xiaobai,62),54,dt);
      render(jimao,ts);
    }
    function updateAnimals(now,dt,ts){
      updateDog(ts,dt);
      updateXiaobai(now,dt,ts);
      updateJimao(now,dt,ts);
      choosePatrol(squirrel,createBusy()?['f1a','furnaceJ','f2a']:['furnaceJ','westJ','f2a'],60,now,'nextWalk');advance(squirrel,dt,ts);render(squirrel,ts);
      var qualityRequested=/quality|blind_oos/.test(pipelineStage)||now<qualityWakeUntil;
      var catAwake=qualityRequested||cat.moving;
      if(cat.awakeState!==catAwake){
        cat.awakeState=catAwake;
        cat.img.src=catAwake?ASSET+'manor-cat-iso-awake-v5.webp':ASSET+'manor-cat-iso-sleep-v5.webp';
      }
      cat.el.classList.toggle('is-sleeping',!catAwake);
      if(qualityRequested)choosePatrol(cat,['windA','windWork','bakeryA'],35,now,'nextWalk');
      advance(cat,dt,ts);render(cat,ts);
      choosePatrol(rabbit,['greenhouseWork','optimizerWork','lowerW1'],42,now,'nextWalk');advance(rabbit,dt,ts);render(rabbit,ts);
      choosePatrol(duck,['gazeboWest','gazeboSouth','gazeboSE','gazeboGate'],36,now,'nextWalk');advance(duck,dt,ts);render(duck,ts);
      choosePatrol(hedgehog,['archiveA','archiveWork','lowerW2'],30,now,'nextWalk');advance(hedgehog,dt,ts);render(hedgehog,ts);
      choosePatrol(owl,['libraryWork','plazaE','bakeryA'],26,now,'nextWalk');advance(owl,dt,ts);render(owl,ts);
    }
    function closedCatmull(points,t){
      var n=points.length,scaled=((t%1)+1)%1*n,i=Math.floor(scaled),u=scaled-i;
      return catmull(points[(i-1+n)%n],points[i%n],points[(i+1)%n],points[(i+2)%n],u);
    }
    function updateButterflies(dt,ts){
      butterflies.forEach(function(fly,index){
        fly.t=(fly.t+fly.speed*dt)%1;var p=closedCatmull(butterflyLoops[index],fly.t);
        var bob=Math.sin(ts/(150+index*35))*5;
        var left=(p[0]|0)+'px',top=((p[1]+bob)|0)+'px',z=depthZ(p[1])+200;
        if(fly._l!==left){fly.el.style.left=left;fly._l=left}
        if(fly._t!==top){fly.el.style.top=top;fly._t=top}
        if(fly._z!==z){fly.el.style.zIndex=String(z);fly._z=z}
      });
    }
    function sceneBusy(){
      return !!(girl.moving||dog.moving||cat.moving||squirrel.moving||xiaobai.moving||jimao.moving||
        rabbit.moving||duck.moving||hedgehog.moving||owl.moving||girlGaiting());
    }
    function tick(ts){
      if(!active){raf=0;return}
      if(document.hidden){raf=0;return}
      var minGap=sceneBusy()?33:80;
      if(lastPaint&&(ts-lastPaint)<minGap){raf=requestAnimationFrame(tick);return}
      var dt=lastTs?Math.min(.05,(ts-lastTs)/1000):.016;lastTs=ts;lastPaint=ts;
      updateGirl(Date.now(),dt,ts);render(girl,ts);updateAnimals(Date.now(),dt,ts);updateButterflies(dt,ts);
      raf=requestAnimationFrame(tick);
    }
    function setActive(next){
      next=!!next&&!document.hidden&&!reduced;
      if(active===next)return;active=next;lastTs=0;lastPaint=0;
      layer.classList.toggle('is-paused',!next);
      if(next&&!raf)raf=requestAnimationFrame(tick);
      if(!next&&raf){cancelAnimationFrame(raf);raf=0}
    }
    function onPipelines(data){
      var pipes=(data&&data.pipelines)||[];
      var prev=girlWorkFamily();
      busyCreatePipes=[];pipelineStage='';
      var activeStages=[];
      pipes.forEach(function(pipe){
        var id=pipeNum(pipe);
        if(!(pipe&&pipe.working))return;
        if(id!==1&&id!==2)return;
        busyCreatePipes.push(id);
        activeStages.push(String(pipe.progress&&pipe.progress.stage||''));
      });
      pipelineStage=activeStages.filter(function(stage){return /quality|blind_oos|handoff/.test(stage)})[0]||activeStages[0]||'';
      maybeInterruptWork(prev);
      markWorkActivity();
    }
    function onWorkshop(data){
      var pipes=(data&&data.pipelines)||[];
      var prev=girlWorkFamily();
      busyWorkshopPipes=[];
      pipes.forEach(function(pipe){
        var id=pipeNum(pipe);
        if(pipe&&pipe.working&&(id===3||id===4))busyWorkshopPipes.push(id);
      });
      if(!busyWorkshopPipes.length&&Number(data&&data.working||0)>0)busyWorkshopPipes.push(3);
      maybeInterruptWork(prev);
      markWorkActivity();
    }
    function onQuality(data){
      var stamp=String(data&&data.updated_at||'')+'|'+String((data&&data.passed||[]).length)+'|'+String((data&&data.failed||[]).length);
      if(qualitySignature&&stamp!==qualitySignature)qualityWakeUntil=Date.now()+9000;
      qualitySignature=stamp;
    }
    function bump(){lastActivity=Date.now()}
    document.addEventListener('pointerdown',bump,{passive:true});document.addEventListener('keydown',bump);
    render(girl,0);render(dog,0);render(cat,0);render(squirrel,0);
    render(xiaobai,0);render(jimao,0);
    render(rabbit,0);render(duck,0);render(hedgehog,0);render(owl,0);
    bindPets();
    bindGirl();
    return {setActive:setActive,onPipelines:onPipelines,onWorkshop:onWorkshop,onQuality:onQuality,bump:bump,
      debug:function(){return {girl:{x:girl.x,y:girl.y,pose:girl.pose,moving:girl.moving},busyCreatePipes:busyCreatePipes.slice(),busyWorkshopPipes:busyWorkshopPipes.slice(),stage:pipelineStage,queue:girlQueue.length}}};
  }

  function createLifeLayer(){
    var layer=el('div','mdq-manor-actors');layer.setAttribute('aria-hidden','true');
    appendOccluders(layer);
    appendFurnaceFx(layer);
    world.appendChild(layer);
    actorEngine=createManorActorEngine(layer);
  }

  function createControls(){
    var hud=el('nav','mdq-manor-hud');
    hud.setAttribute('aria-label','庄园地图控制');
    [
      ['−','缩小',function(){zoomAt(.82)}],
      ['⌂','回到庄园全景',fitCamera],
      ['+','放大',function(){zoomAt(1.2)}]
    ].forEach(function(item){
      var button=el('button','',item[0]);
      button.type='button';
      button.title=item[1];
      button.setAttribute('aria-label',item[1]);
      button.addEventListener('click',item[2]);
      hud.appendChild(button);
    });
    root.appendChild(hud);

    var districts=el('nav','mdq-manor-districts');
    districts.setAttribute('aria-label','庄园分区导航');
    zones.forEach(function(zone){
      var button=el('button','',zone.short);
      button.type='button';
      button.title='查看'+zone.label+'状态';
      button.addEventListener('click',function(){openZoneStatus(zone)});
      districts.appendChild(button);
    });
    root.appendChild(districts);
    root.appendChild(el('div','mdq-manor-legend','点区域看协同状态 · 悬停建筑查看标题 · 手机轻点查看、再点进入 · 拖动/缩放游览'));
  }

  function createDrawer(){
    scrim=el('div','mdq-manor-scrim');
    scrim.addEventListener('click',closeDrawer);
    root.appendChild(scrim);
    drawer=el('aside','mdq-manor-drawer');
    drawer.setAttribute('role','dialog');
    drawer.setAttribute('aria-modal','true');
    drawer.setAttribute('aria-hidden','true');
    drawer.inert=true;
    var head=el('div','mdq-manor-drawer-head');
    drawerTitle=el('div','mdq-manor-drawer-title','庄园建筑');
    var close=el('button','mdq-manor-drawer-close','×');
    close.type='button';
    close.setAttribute('aria-label','关闭');
    close.addEventListener('click',closeDrawer);
    head.appendChild(drawerTitle);
    head.appendChild(close);
    drawerHost=el('div','mdq-manor-drawer-host');
    drawer.appendChild(head);
    drawer.appendChild(drawerHost);
    root.appendChild(drawer);
  }

  function createWorld(){
    root=el('section','mdq-manor-root');
    root.id='mdqManorView';
    root.setAttribute('aria-label','马卡龙与大福量化庄园');
    bootUi=createBootOverlay();
    root.appendChild(bootUi.el);
    viewport=el('div','mdq-manor-viewport');
    viewport.id='mdqManorViewport';
    world=el('div','mdq-manor-world');
    world.id='mdqManorWorld';
    var background=hideBroken(el('img','mdq-manor-map'));
    background.decoding='async';
    background.loading='eager';
    background.setAttribute('fetchpriority','high');
    background.src='/housekeeper/manor-world-v7.webp';
    background.alt='';
    background.draggable=false;
    world.appendChild(background);
    world.appendChild(el('div','mdq-manor-vignette'));
    createRouteLayer();
    createZones();
    labelLayer=el('div','mdq-building-labels');
    labelLayer.setAttribute('aria-hidden','true');
    specs.forEach(createBuilding);
    createDepthScene();
    createLifeLayer();
    world.appendChild(labelLayer);
    viewport.appendChild(world);
    root.appendChild(viewport);
    createControls();
    createDrawer();
    document.body.insertBefore(root,q('body>.grid'));
    bindCamera();
    observeStates();
    refreshZoneMarkers();
    setInterval(refreshZoneMarkers,45000);
    startManorBoot();
  }

  function createBootOverlay(){
    var wrap=el('div','mdq-manor-boot');
    wrap.setAttribute('role','status');
    wrap.setAttribute('aria-live','polite');
    wrap.appendChild(el('div','mdq-manor-boot-title','加载中'));
    var bar=el('div','mdq-manor-boot-bar');
    var fill=el('i','mdq-manor-boot-fill');
    bar.appendChild(fill);
    wrap.appendChild(bar);
    var meta=el('div','mdq-manor-boot-meta','准备庄园…');
    wrap.appendChild(meta);
    return {el:wrap,fill:fill,meta:meta};
  }

  function startManorBoot(){
    var finished=false,done=0;
    var extraPoses=[
      '/housekeeper/manor-cat-iso-awake-v5.webp',
      '/housekeeper/manor-xiaobai-wave-v6p.webp','/housekeeper/manor-xiaobai-paw-v6p.webp','/housekeeper/manor-xiaobai-sit-v6p.webp',
      '/housekeeper/manor-jimao-wave-v6p.webp','/housekeeper/manor-jimao-paw-v6p.webp','/housekeeper/manor-jimao-sit-v6p.webp'
    ];
    function criticalImages(){
      return [].slice.call(world.querySelectorAll('.mdq-manor-map, .mdq-depth-slice img, .mdq-chimney-clear, .mdq-hearth-live, .mdq-actor-base'));
    }
    function paint(label,pct){
      if(!bootUi)return;
      if(pct===undefined)pct=0;
      bootUi.fill.style.width=Math.max(0,Math.min(100,pct))+'%';
      bootUi.meta.textContent=label;
    }
    function delay(ms){return new Promise(function(resolve){setTimeout(resolve,ms)})}
    function waitImage(img){
      return new Promise(function(resolve,reject){
        var settled=false;
        var timer=setTimeout(function(){fail()},7000);
        function fail(){if(settled)return;settled=true;clearTimeout(timer);reject(new Error('boot-image-failed'))}
        function ok(){
          if(settled)return;
          if(!imageUsable(img)){fail();return}
          settled=true;clearTimeout(timer);resolve(img);
        }
        img.addEventListener('load',ok,{once:true});
        img.addEventListener('error',fail,{once:true});
        if(img.complete){if(imageUsable(img))ok();else fail()}
      });
    }
    function loadCritical(img){
      return waitImage(img).catch(function(){
        var src=canonSrc(img);
        if(!src)return img;
        img.dataset.bootBroken='';
        img.style.visibility='';
        img.src=src+'?boot='+Date.now();
        return delay(200).then(function(){return waitImage(img)}).catch(function(){return img});
      });
    }
    function hydrateDeferred(){
      [].slice.call(world.querySelectorAll('img[data-src]')).forEach(function(img){
        if(img.getAttribute('src'))return;
        img.decoding='async';
        img.src=img.dataset.src;
      });
      extraPoses.forEach(function(src){ /* skip GPU preload; poses load on demand */ });
    }
    function reveal(){
      if(finished)return;
      finished=true;
      paint('正在打开庄园…',100);
      manorReady=true;
      root.classList.add('is-ready');
      if(document.body.classList.contains('mdq-manor-mode')&&actorEngine)actorEngine.setActive(true);
      hydrateDeferred();
      setTimeout(function(){if(bootUi&&bootUi.el&&bootUi.el.parentNode)bootUi.el.parentNode.removeChild(bootUi.el)},280);
    }
    var list=criticalImages();
    var total=list.length||1;
    paint('正在装入庄园…',0);
    Promise.all(list.map(function(img){
      return loadCritical(img).then(function(){
        done+=1;
        var pct=Math.round(done/total*100);
        paint('已就绪 '+done+' / '+total+'　'+pct+'%',pct);
      });
    })).then(reveal);
    setTimeout(function(){
      var map=world.querySelector('.mdq-manor-map');
      if(!finished&&imageUsable(map))reveal();
    },8000);
  }

  function setMode(mode,persist){
    if(mode!=='manor')mode='classic';
    if(mode==='classic')closeDrawer();
    clearBuildingReveal();
    document.body.classList.toggle('mdq-manor-mode',mode==='manor');
    if(switcher){
      switcher.querySelectorAll('button[data-mode]').forEach(function(button){
        button.setAttribute('aria-pressed',String(button.dataset.mode===mode));
      });
    }
    if(persist){
      try{localStorage.setItem(STORAGE_KEY,mode)}catch(ignore){}
    }
    if(actorEngine)actorEngine.setActive(mode==='manor'&&manorReady);
    if(mode==='manor')requestAnimationFrame(fitCamera);
    try{document.dispatchEvent(new CustomEvent('mdq:mode',{detail:mode}))}catch(ignore){}
  }

  function restoreModule(){
    var items=activeModules&&activeModules.length?activeModules.slice():[];
    if(!items.length&&activeModule){
      items=[{node:activeModule,marker:activeMarker}];
    }
    items.forEach(function(item){
      if(item.node){
        item.node.querySelectorAll('.mdq-pipe-focus').forEach(function(node){node.classList.remove('mdq-pipe-focus')});
        if(item.node.getAttribute('data-mdq-was-hidden')){
          item.node.setAttribute('hidden','');
          item.node.removeAttribute('data-mdq-was-hidden');
        }
      }
      if(item.marker&&item.marker.parentNode&&item.node){
        item.marker.parentNode.insertBefore(item.node,item.marker);
      }
      if(item.marker&&item.marker.parentNode)item.marker.remove();
    });
    activeModule=null;
    activeMarker=null;
    activeModules=[];
  }

  function openModule(spec,trigger){
    if(!spec)return;
    clearZoneStatusPanel();
    var selectors=[spec.target].concat(spec.extras||[]);
    var nodes=[];
    selectors.forEach(function(sel){
      var node=sel?q(sel):null;
      if(node)nodes.push(node);
    });
    if(!nodes.length){
      setBuilding(spec.id,'error','模块暂不可用');
      return;
    }
    clearBuildingReveal();
    restoreModule();
    activeTrigger=trigger;
    activeModules=nodes.map(function(node,i){
      var marker=document.createComment('mdq-manor-module-slot:'+spec.id+':'+i);
      node.parentNode.insertBefore(marker,node);
      if(node.hasAttribute('hidden')){
        node.setAttribute('data-mdq-was-hidden','1');
        node.removeAttribute('hidden');
      }
      drawerHost.appendChild(node);
      return {node:node,marker:marker};
    });
    activeModule=nodes[0];
    activeMarker=activeModules[0].marker;
    drawerTitle.textContent=spec.title;
    drawer.classList.add('is-open');
    scrim.classList.add('is-open');
    drawer.setAttribute('aria-hidden','false');
    drawer.inert=false;
    if(spec.focus){
      requestAnimationFrame(function(){
        var focus=q(spec.focus,activeModule);
        if(focus){
          focus.classList.add('mdq-pipe-focus');
          focus.scrollIntoView({block:'center',behavior:'smooth'});
        }
      });
    }
    if(spec.id==='backtest' && typeof window.loadManualExperience==='function'){
      try{ window.loadManualExperience(); }catch(e){}
    }
  }

  function closeDrawer(){
    if(!drawer)return;
    drawer.classList.remove('is-open');
    scrim.classList.remove('is-open');
    drawer.setAttribute('aria-hidden','true');
    drawer.inert=true;
    clearBuildingReveal();
    restoreModule();
    clearZoneStatusPanel();
    if(activeTrigger&&activeTrigger.focus)activeTrigger.focus({preventScroll:true});
    activeTrigger=null;
  }

  function applyCamera(){
    world.style.transform='translate3d('+Math.round(camera.x)+'px,'+Math.round(camera.y)+'px,0) scale('+camera.scale+')';
  }
  function coverScale(){
    if(!viewport) return 1;
    var vw=viewport.clientWidth||1;
    var vh=viewport.clientHeight||1;
    // Half-pixel extra so rounding never leaves a strip of outside background.
    return Math.max((vw+0.5)/WORLD_W,(vh+0.5)/WORLD_H);
  }
  function scaleLimits(){
    var floor=coverScale();
    return {min:floor, max:Math.max(floor, camera.max)};
  }
  function clampCamera(){
    if(!viewport)return;
    var vw=viewport.clientWidth;
    var vh=viewport.clientHeight;
    var limits=scaleLimits();
    if(camera.scale<limits.min) camera.scale=limits.min;
    if(camera.scale>limits.max) camera.scale=limits.max;
    var ww=WORLD_W*camera.scale;
    var wh=WORLD_H*camera.scale;
    camera.x=Math.min(0,Math.max(vw-ww,camera.x));
    camera.y=Math.min(0,Math.max(vh-wh,camera.y));
  }
  function fitCamera(){
    if(!viewport)return;
    var vw=viewport.clientWidth;
    var vh=viewport.clientHeight;
    camera.scale=coverScale();
    camera.x=(vw-WORLD_W*camera.scale)/2;
    camera.y=(vh-WORLD_H*camera.scale)/2;
    clampCamera();
    applyCamera();
  }
  function clearZoneStatusPanel(){
    if(zoneStatusPanel&&zoneStatusPanel.parentNode){
      zoneStatusPanel.parentNode.removeChild(zoneStatusPanel);
    }
    zoneStatusPanel=null;
  }

  function escapeHtml(text){
    return String(text==null?'':text)
      .replace(/&/g,'&amp;')
      .replace(/</g,'&lt;')
      .replace(/>/g,'&gt;')
      .replace(/"/g,'&quot;');
  }

  function healthZh(health){
    if(health==='active')return '进行中';
    if(health==='warning')return '需留意';
    if(health==='error')return '阻塞';
    return '空闲';
  }

  function kindZh(kind){
    var map={process:'进程',change:'变更',deploy:'部署',repair:'维修',experiment:'试验'};
    return map[kind]||kind||'事项';
  }

  function renderEventCard(row,role){
    var docs=(row.doc_paths||[]).map(function(p){
      return '<li>'+escapeHtml(p)+'</li>';
    }).join('');
    var services=(row.services||[]).map(function(s){return escapeHtml(s)}).join(' · ');
    var detail=row.detail_zh?('<details class="mdq-zone-detail"><summary>详情</summary><pre>'+escapeHtml(row.detail_zh)+'</pre></details>'):'';
    var docsBlock=docs?('<div class="mdq-zone-docs"><span>附属文档</span><ul>'+docs+'</ul></div>'):'';
    var src=row.source==='auto'?' · 自动':'';
    return ''+
      '<article class="mdq-zone-event is-'+escapeHtml(role||row.kind||'change')+(row.source==='auto'?' is-auto':'')+'">'+
        '<header><strong>'+escapeHtml(row.title_zh||row.summary_zh||'未命名')+'</strong>'+
        '<em>'+escapeHtml(kindZh(row.kind))+' · '+escapeHtml(row.status||'')+src+'</em></header>'+
        '<p>'+escapeHtml(row.summary_zh||'')+'</p>'+
        '<footer>'+
          '<span>'+escapeHtml(row.actor||'—')+'</span>'+
          '<span>'+escapeHtml(row.updated_at||row.started_at||'')+'</span>'+
          (row.git_sha?('<span>sha '+escapeHtml(String(row.git_sha).slice(0,10))+'</span>'):'')+
          (services?('<span>'+services+'</span>'):'')+
        '</footer>'+
        detail+docsBlock+
      '</article>';
  }

  function fillZoneStatusBody(panel, data){
    var body=panel.querySelector('.mdq-zone-status-body');
    if(!body)return;
    if(!data||!data.ok){
      body.innerHTML='<div class="atm-empty">区域状态读取失败'+(data&&data.error?('：'+escapeHtml(data.error)):'')+'</div>';
      return;
    }
    var zone=data.zone||{};
    var mods=(zone.modules||[]).map(function(m){
      return '<button type="button" class="mdq-zone-chip" data-building="'+escapeHtml(m.manor_building||'')+'">'+escapeHtml(m.title_zh||m.id)+'</button>';
    }).join('');
    var procs=(data.processes||[]).map(function(row){return renderEventCard(row,'process')}).join('')
      || '<div class="atm-empty">当前无进行中进程</div>';
    var changes=(data.changes||[]).map(function(row){return renderEventCard(row,'change')}).join('')
      || '<div class="atm-empty">暂无后端变更记录</div>';
    body.innerHTML=''+
      '<div class="mdq-zone-health is-'+escapeHtml(data.health||'ok')+'">状态 · '+escapeHtml(healthZh(data.health))+'</div>'+
      '<div class="mdq-zone-modules"><span>下辖</span><div class="mdq-zone-chips">'+mods+'</div></div>'+
      '<section class="mdq-zone-block"><h3>正在进行</h3>'+procs+'</section>'+
      '<section class="mdq-zone-block"><h3>后端变更</h3>'+changes+'</section>'+
      '<div class="mdq-zone-actions">'+
        '<button type="button" class="mdq-zone-focus-btn">定位到本区</button>'+
      '</div>'+
      '<div class="mdq-zone-meta">更新 '+(escapeHtml(data.updated_at||''))+'</div>';
  }

  function openZoneStatus(zone,trigger){
    if(!zone||!drawer)return;
    clearBuildingReveal();
    restoreModule();
    clearZoneStatusPanel();
    activeTrigger=trigger||null;
    var panel=el('div','mdq-zone-status atm-section');
    panel.setAttribute('data-zone',zone.id);
    panel.innerHTML=''+
      '<div class="atm-section-head"><div><h2 class="atm-section-title">'+escapeHtml(zone.label)+'</h2>'+
      '<div class="atm-section-sub">区域协同状态 · 进程与后端变更（不跳转业务面板）</div></div></div>'+
      '<div class="mdq-zone-status-body"><div class="atm-empty">正在读取区域状态…</div></div>';
    zoneStatusPanel=panel;
    drawerHost.appendChild(panel);
    drawerTitle.textContent=zone.label+' · 状态';
    drawer.classList.add('is-open');
    scrim.classList.add('is-open');
    drawer.setAttribute('aria-hidden','false');
    drawer.inert=false;

    panel.addEventListener('click',function(ev){
      var chip=ev.target.closest('.mdq-zone-chip');
      if(chip&&chip.getAttribute('data-building')){
        var bid=chip.getAttribute('data-building');
        var spec=specs.filter(function(item){return item.id===bid})[0];
        if(spec&&!spec.placeholder)openModule(spec,q('[data-manor-id="'+bid+'"]'));
        return;
      }
      if(ev.target.closest('.mdq-zone-focus-btn')){
        focusArea(zone);
      }
    });

    // Paint immediately so Flask queue cannot leave the card on “正在读取…”
    var cached=zoneStatusCache[zone.id];
    if(cached&&cached.ok){
      fillZoneStatusBody(panel, cached);
    }else{
      fillZoneStatusBody(panel, {
        ok:true,
        health:'ok',
        zone:{
          id:zone.id,
          label_zh:zone.label,
          modules:ZONE_MODULE_FALLBACK[zone.id]||[]
        },
        processes:[],
        changes:[],
        updated_at:''
      });
      var body=panel.querySelector('.mdq-zone-status-body');
      if(body){
        var meta=body.querySelector('.mdq-zone-meta');
        if(meta)meta.textContent='正在刷新最新状态…';
        else{
          var hint=el('div','mdq-zone-meta','正在刷新最新状态…');
          body.appendChild(hint);
        }
      }
    }

    var url=zoneApiBase+'/'+encodeURIComponent(zone.id)+'/status';
    var ctrl=(typeof AbortController!=='undefined')?new AbortController():null;
    var timer=ctrl?setTimeout(function(){try{ctrl.abort();}catch(e){}},12000):null;
    var opts=ctrl?{signal:ctrl.signal,credentials:'same-origin'}:{credentials:'same-origin'};
    fetch(url, opts).then(function(r){
      return r.json().catch(function(){return {};}).then(function(d){
        if(!r.ok){throw new Error((d&&(d.error||d.message))||('请求失败 '+r.status));}
        return d;
      });
    }).then(function(data){
      if(zoneStatusPanel!==panel)return;
      if(data&&data.ok)zoneStatusCache[zone.id]=data;
      fillZoneStatusBody(panel, data);
    }).catch(function(err){
      if(zoneStatusPanel!==panel)return;
      if(cached&&cached.ok){
        fillZoneStatusBody(panel, cached);
        var body=panel.querySelector('.mdq-zone-status-body');
        if(body){
          var note=el('div','mdq-zone-meta','刷新失败，显示上次状态 · '+((err&&err.message)||String(err)));
          body.appendChild(note);
        }
        return;
      }
      fillZoneStatusBody(panel, {ok:false, error:(err&&err.message)||String(err)});
    }).then(function(){
      if(timer)clearTimeout(timer);
    });
  }

  function focusArea(zone){
    if(!viewport||!zone)return;
    var limits=scaleLimits();
    var scale=window.innerWidth<760?Math.max(limits.min,1.02):zone.scale;
    scale=Math.max(limits.min,Math.min(limits.max,scale));
    camera.scale=scale;
    camera.x=viewport.clientWidth/2-zone.cx*scale;
    camera.y=viewport.clientHeight/2-zone.cy*scale;
    clampCamera();
    applyCamera();
  }
  function zoomAt(factor,clientX,clientY){
    var rect=viewport.getBoundingClientRect();
    var cx=clientX===undefined?rect.width/2:clientX-rect.left;
    var cy=clientY===undefined?rect.height/2:clientY-rect.top;
    var old=camera.scale;
    var limits=scaleLimits();
    var next=Math.max(limits.min,Math.min(limits.max,old*factor));
    var wx=(cx-camera.x)/old;
    var wy=(cy-camera.y)/old;
    camera.scale=next;
    camera.x=cx-wx*next;
    camera.y=cy-wy*next;
    clampCamera();
    applyCamera();
  }

  function bindCamera(){
    viewport.addEventListener('wheel',function(event){
      event.preventDefault();
      zoomAt(event.deltaY>0?.88:1.14,event.clientX,event.clientY);
    },{passive:false});
    viewport.addEventListener('pointerdown',function(event){
      var building=event.target.closest('.mdq-building');
      if(event.target.closest('.mdq-zone-marker,.mdq-xiaobai,.mdq-jimao,.mdq-girl'))return;
      if(!building)clearBuildingReveal();
      pointers.set(event.pointerId,{x:event.clientX,y:event.clientY,building:building});
      if(pointers.size===1){
        dragStart={x:event.clientX,y:event.clientY,cx:camera.x,cy:camera.y,moved:false,building:building,pointerId:event.pointerId};
        if(!building)viewport.setPointerCapture(event.pointerId);
      }else if(pointers.size===2){
        var values=Array.from(pointers.values());
        pinch={d:Math.hypot(values[0].x-values[1].x,values[0].y-values[1].y),scale:camera.scale};
        dragStart=null;
        suppressClickUntil=Date.now()+450;
        clearBuildingReveal();
        pointers.forEach(function(value,pointerId){
          try{viewport.setPointerCapture(pointerId)}catch(ignore){}
        });
      }
      viewport.classList.add('is-dragging');
    });
    viewport.addEventListener('pointermove',function(event){
      if(!pointers.has(event.pointerId))return;
      pointers.set(event.pointerId,{x:event.clientX,y:event.clientY});
      if(pointers.size===1&&dragStart){
        var dx=event.clientX-dragStart.x;
        var dy=event.clientY-dragStart.y;
        if(Math.hypot(dx,dy)>7&&!dragStart.moved){
          dragStart.moved=true;
          suppressClickUntil=Date.now()+450;
          clearBuildingReveal();
          try{viewport.setPointerCapture(event.pointerId)}catch(ignore){}
        }
        if(dragStart.moved||!dragStart.building){
          camera.x=dragStart.cx+dx;
          camera.y=dragStart.cy+dy;
          clampCamera();
          applyCamera();
        }
      }else if(pointers.size===2&&pinch){
        var values=Array.from(pointers.values());
        var distance=Math.hypot(values[0].x-values[1].x,values[0].y-values[1].y);
        var midX=(values[0].x+values[1].x)/2;
        var midY=(values[0].y+values[1].y)/2;
        var desired=pinch.scale*distance/pinch.d;
        zoomAt(desired/camera.scale,midX,midY);
      }
    });
    function end(event){
      if((dragStart&&dragStart.moved)||pinch)suppressClickUntil=Date.now()+450;
      pointers.delete(event.pointerId);
      dragStart=null;
      pinch=null;
      if(!pointers.size)viewport.classList.remove('is-dragging');
    }
    viewport.addEventListener('pointerup',end);
    viewport.addEventListener('pointercancel',end);
    window.addEventListener('resize',function(){if(document.body.classList.contains('mdq-manor-mode'))fitCamera()});
    document.addEventListener('keydown',function(event){
      if(event.key==='Escape')closeDrawer();
      if(!document.body.classList.contains('mdq-manor-mode')||drawer.classList.contains('is-open'))return;
      if(event.key==='+'||event.key==='=')zoomAt(1.15);
      if(event.key==='-')zoomAt(.87);
    });
    document.addEventListener('visibilitychange',function(){
      var manorOn=document.body.classList.contains('mdq-manor-mode')&&manorReady;
      root.classList.toggle('is-paused',document.hidden||!manorOn);
      clearBuildingReveal();
      if(actorEngine)actorEngine.setActive(manorOn&&!document.hidden);
      if(!document.hidden&&manorOn)requestAnimationFrame(fitCamera);
    });
  }

  var pipeLiveOverride={1:false,2:false,3:false,4:false,5:false,6:false,7:false,8:false};
  var pipeLiveHeard=false;

  function furnaceBusyText(text){
    text=String(text||'');
    if(/状态获取失败|关/.test(text))return false;
    return /正在工作|工作中|研究中|在研|计算中|处理中|发明中|评测/.test(text);
  }

  function applyFurnaceLive(live1,live2){
    if(!root)return;
    root.classList.toggle('has-furnace1-live',!!live1);
    root.classList.toggle('has-furnace2-live',!!live2);
  }

  function syncFurnaceLive(){
    if(pipeLiveHeard){
      var left=pipeLiveOverride[1]||pipeLiveOverride[2]||pipeLiveOverride[3]||pipeLiveOverride[4];
      var right=pipeLiveOverride[5]||pipeLiveOverride[6]||pipeLiveOverride[7]||pipeLiveOverride[8];
      applyFurnaceLive(left,right);
      return;
    }
    var leftText=[1,2,3,4].map(function(n){return textOf('atmPipe'+n+'State');}).join(' ');
    var rightText=[5,6,7,8].map(function(n){return textOf('atmPipe'+n+'State');}).join(' ');
    applyFurnaceLive(furnaceBusyText(leftText),furnaceBusyText(rightText));
  }

  function setBuilding(id,state,text){
    var button=q('button.mdq-building[data-manor-id="'+id+'"]',world);
    if(!button)return;
    button.classList.remove('is-loading','is-idle','is-active','is-ok','is-warning','is-error');
    button.classList.add('is-'+state);
    var label=buildingLabels[id];
    var status=label?q('.mdq-building-status',label):null;
    if(status&&text)status.textContent=text;
    var title=label?q('.mdq-building-title',label):null;
    button.setAttribute('aria-label',(title?title.textContent:id)+'：'+(text||state));
  }

  function rowCount(id){
    var node=document.getElementById(id);
    if(!node)return 0;
    return Array.from(node.querySelectorAll('tr')).filter(function(row){
      return !row.querySelector('[colspan]')&&(row.textContent||'').trim();
    }).length;
  }

  function syncStates(){
    var leftBusy=false,rightBusy=false;
    var leftBits=[],rightBits=[];
    [1,2,3,4].forEach(function(number){
      var state=textOf('atmPipe'+number+'State');
      var pct=textOf('atmPipe'+number+'Pct');
      leftBits.push([state,pct].filter(Boolean).join(' · '));
      if(furnaceBusyText(state))leftBusy=true;
    });
    [5,6,7,8].forEach(function(number){
      var state=textOf('atmPipe'+number+'State');
      var pct=textOf('atmPipe'+number+'Pct');
      rightBits.push([state,pct].filter(Boolean).join(' · '));
      if(furnaceBusyText(state))rightBusy=true;
    });
    setBuilding('furnace1',leftBusy?'active':'idle',leftBusy?('在研 · '+leftBits.filter(Boolean)[0]):'车道1–2 · 单一系统');
    setBuilding('furnace2',rightBusy?'active':'idle',rightBusy?('在研 · '+rightBits.filter(Boolean)[0]):'hub-b 已退役');
    root.classList.toggle('has-creation-flow',leftBusy||rightBusy);
    syncFurnaceLive();

    var confirmCount=textOf('atmConfirmCount');
    var review=textOf('atmReviewStatus');
    setBuilding('quality',inferState(review),confirmCount||'0 份等人确认');
    root.classList.toggle('has-quality-work',/确认|等人|审批|轮/.test(review+confirmCount)&&!/队列为空|0 份/.test(confirmCount||''));
    setBuilding('bakery','ok',textOf('atmStrategyCount')||textOf('atmBakeryCount')||'运行策略读取中');
    var outletLabel=textOf('atmInventOutletCount')||'发明口待命';
    setBuilding('experimental',inferState(outletLabel),outletLabel);
    root.classList.toggle('has-workshop-flow',false);
    setBuilding('forecast',inferState(textOf('systemForecastPanel')),textOf('atmFcGeoWeekly')?('周几何增长率 '+textOf('atmFcGeoWeekly')):'组合数据读取中');
    setBuilding('strategies',inferState(textOf('atmArchiveStatus')),textOf('atmArchiveStatus')||'未到起始计数时间');
    var holdText=textOf('atmHoldAssistCount')||'';
    var holdN=(holdText.match(/(\d+)\s*条/)||[])[1];
    var holdBusy=!!(holdN&&parseInt(holdN,10)>0)||!!document.querySelector('#atmHoldAssistRoster .atm-row, #atmHoldAssistRoster .atm-pos-card, #atmHoldAssistRoster details');
    setBuilding('market',holdBusy?'active':inferState(textOf('atmHoldAssistRoster')+' '+holdText),holdText||'观风塔待命');
    var posCountText=textOf('atmPositionCount')||'';
    var posMatch=posCountText.match(/(\d+)\s*个仓位/);
    var openPos=!!(posMatch&&parseInt(posMatch[1],10)>0);
    if(!openPos)openPos=!!document.querySelector('#atmPositionRoster .atm-pos-card');
    root.classList.toggle('has-open-positions',openPos);
    setBuilding('pos_greenhouse',openPos?'active':inferState(textOf('atmPositionRoster')),posCountText||'仓位读取中');
    setBuilding('pos_slot','idle','占位');
    setBuilding('backtest',inferState(textOf('mdqExpCount')+' '+textOf('mdqSincereCount')),textOf('mdqSincereCount')||textOf('mdqExpCount')||'溪栖工作室待命');
    setBuilding('actions',inferState(textOf('safetyNetBox')),textOf('safetyNetManorStatus')||'系统操作 · 安全网');
    setBuilding('timeline','ok','系统大事年表');
    setBuilding('market_sign',inferState(textOf('dialysisBox')),textOf('marketMonitorTime')||'盘面读取中');
    var health=textOf('processBox');
    setBuilding('health',inferState(health),/异常|错误|未运行/.test(health)?'检测到服务异常':'核心服务正常');
  }

  function observeStates(){
    var targetIds=[
      'atmPipe1State','atmPipe2State','atmPipe1Pct','atmPipe2Pct','atmPipe3State','atmPipe4State',
      'atmConfirmCount','atmReviewStatus','atmBakeryCount','atmBakeryStatus',
      'atmPendingOptimizeCount','atmPendingOptimizeStatus','atmWorkshopBatch','systemForecastPanel','atmFcGeoWeekly',
      'atmStrategyCount','atmStrategyRoster','atmPositionCount','atmPositionRoster','atmHoldAssistCount','atmHoldAssistRoster','btProgress','marketMonitorTime',
      'dialysisBox','processBox','safetyNetBox','safetyNetManorStatus','atmArchiveStatus','atmDayOpenAuto','atmWkOpenAuto','experimentalStrategies','mdqExpCount','mdqSincereCount'
    ];
    var queued=false;
    var observer=new MutationObserver(function(){
      if(queued)return;
      queued=true;
      requestAnimationFrame(function(){queued=false;syncStates()});
    });
    targetIds.forEach(function(id){
      var node=document.getElementById(id);
      if(node)observer.observe(node,{childList:true,subtree:true,characterData:true,attributes:true});
    });
    document.addEventListener('hk:pipelines',function(event){
      var pipes=(event.detail&&event.detail.pipelines)||[];
      root.classList.toggle('has-creation-flow',pipes.some(function(pipe){
        return pipe&&pipe.working;
      }));
      if(actorEngine)actorEngine.onPipelines(event.detail||{});
      pipeLiveOverride={1:false,2:false,3:false,4:false,5:false,6:false,7:false,8:false};
      pipeLiveHeard=true;
      pipes.forEach(function(pipe){
        var id=Number(pipe&&(pipe.pipeline||pipe.lane_no)||0);
        if(pipe&&pipe.working&&id>=1&&id<=8)pipeLiveOverride[id]=true;
      });
      syncStates();
    });
    document.addEventListener('hk:invent_outlets',function(){ syncStates(); });
    document.addEventListener('hk:workshop',function(event){
      var detail=event.detail||{};
      root.classList.toggle('has-workshop-flow',false);
      if(actorEngine&&actorEngine.onWorkshop)actorEngine.onWorkshop(detail);
      syncStates();
    });
    document.addEventListener('hk:qi',function(event){
      var detail=event.detail||{};
      if(actorEngine)actorEngine.onQuality(detail);
      syncStates();
    });
    document.addEventListener('hk:confirm_queue',function(event){
      var detail=event.detail||{};
      if(actorEngine)actorEngine.onQuality(detail);
      syncStates();
    });
    document.addEventListener('hk:bakery',function(){
      syncStates();
    });
    syncStates();
  }

  function boot(){
    try{
      setStableModuleIds();
      bindSwitcher();
      createWorld();
      var mode='manor';
      try{mode=localStorage.getItem(STORAGE_KEY)||localStorage.getItem('mdq.display.mode.v1')||'manor'}catch(ignore){}
      setMode(mode,false);
      window.MDQManor=manorApi;
      Object.assign(manorApi,{
        setMode:setMode,
        open:function(id){
          var spec=specs.filter(function(item){return item.id===id})[0];
          if(!spec)return;
          openModule(spec,q('[data-manor-id="'+id+'"]'));
        },
        focus:function(id){
          var zone=zones.filter(function(item){return item.id===id})[0];
          if(zone)openZoneStatus(zone);
        },
        focusCamera:function(id){
          var zone=zones.filter(function(item){return item.id===id})[0];
          if(zone)focusArea(zone);
        },
        openZone:function(id){
          var zone=zones.filter(function(item){return item.id===id})[0];
          if(zone)openZoneStatus(zone);
        },
        refreshZones:refreshZoneMarkers,
        close:closeDrawer,
        reset:fitCamera,
        actors:function(){return actorEngine?actorEngine.debug():null},
        activateBuilding:function(id,pointerType){
          var button=q('[data-manor-id="'+id+'"]');
          var spec=specs.filter(function(item){return item.id===id})[0];
          if(!button||!spec)return 'missing';
          lastBuildingPointer={target:button,type:pointerType||'mouse',at:Date.now()};
          if((pointerType==='touch'||pointerType==='pen')&&revealedBuilding!==button){revealBuilding(button);return 'revealed'}
          clearBuildingReveal();
          if(spec.placeholder){
            button.click();
            return 'placeholder';
          }
          openModule(spec,button);return 'opened';
        }
      });
    }catch(error){
      console.error('Macaron Manor phase 2 init failed',error);
      document.body.classList.remove('mdq-manor-mode');
      if(switcher){
        switcher.querySelectorAll('button[data-mode="manor"]').forEach(function(button){
          button.disabled=true;
          button.title='庄园暂不可用，经典控制台仍可正常使用';
        });
      }
    }
  }

  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot);
  else boot();
})();
