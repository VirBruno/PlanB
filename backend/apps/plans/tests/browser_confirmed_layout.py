"""Detalle confirmado completo, Chrome/Leaflet reales y respuestas locales simuladas."""
import json

from . import browser_recommendations as browser
from django.template.loader import render_to_string
from apps.plans.expense_forms import ExpenseForm
from apps.plans.services.expense_service import summarize
from apps.plans.tests.test_expenses import expense, snapshot
from apps.plans.tests.test_recommendations_ui import confirmed_plan, confirmed_proposal
from apps.plans.tests.test_services import USER_ID

HARNESS = r"""
<script>
window.layoutCalls = [];
window.layoutPanel = __EXPENSE_PANEL__;
window.layoutItems = Array.from({length: 5}, (_, i) => ({
  provider_place_id:'poi-'+i, name:['Café de la Plaza','El Porteño','Rincón','La Esquina','Café Güemes'][i],
  categories:['catering.cafe'], address:'Calle '+(10+i)+', La Plata', latitude:-34.6+i*.002,
  longitude:-58.38+i*.003, distance_meters:100+i*100, rank:i+1,
  google_maps_url:'https://www.google.com/maps/search/?api=1&query=cafe',
  google_search_url:'https://www.google.com/search?q=cafe'
}));
window.layoutGenerated = false;
// Sustituir tiles antes de inicializar los previews, también el hero visible.
document.addEventListener('load',event=>{
  if(event.target.tagName!=='SCRIPT'||!event.target.src.includes('leaflet.js'))return;
  const realTiles=L.tileLayer;
  L.tileLayer=(url,options)=>realTiles('data:image/svg+xml,'+encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256"><rect width="256" height="256" fill="#e9e5db"/><path d="M0 80H256M0 180H256M80 0V256M180 0V256" stroke="#fff" stroke-width="10"/></svg>'),options);
},true);
window.fetch = async (url, options={}) => {
  const path = new URL(url, location.href).pathname;
  const method = options.method || 'GET';
  window.layoutCalls.push({path,method,fields:options.body?[...options.body.keys()]:[]});
  const respond = (payload,status=200) => new Response(JSON.stringify(payload),{status,headers:{'Content-Type':'application/json'}});
  if (path.endsWith('/recomendaciones/')) {
    if (method === 'POST') window.layoutGenerated = true;
    return respond(window.layoutGenerated ? {status:'ready',has_snapshot:true,items:window.layoutItems,
      ai_summary:'Para este encuentro buscamos cafeterías cerca del punto acordado.'} : {status:'not_generated',items:[]});
  }
  if (path.endsWith('/gastos/')) return method === 'POST' ? respond({saved:true},201)
    : new Response(window.layoutPanel,{headers:{'Content-Type':'text/html'}});
  if (path.endsWith('/gastos/notificar/')) return respond({sent:true,message:'Notificación enviada.'});
  if (path.includes('/clima/localidades/')) return respond({cities:[{id:123,name:'La Plata',province:'Buenos Aires'}]});
  if (path.includes('/clima/pronostico/')) return respond({status:'available',city:'La Plata',province:'Buenos Aires',days:
    Array.from({length:16},(_,i)=>({date:'2026-11-'+String(7+i).padStart(2,'0'),condition:i?'Nublado':'Despejado',icon:i?'cloud':'sun',temperature_min:12,temperature_max:24,rain_probability:10,wind_max:12}))});
  if (path.includes('/propuestas/') && path.endsWith('/clima/')) return respond({status:'available',condition:'Parcialmente nublado',icon:'sun-cloud',temperature_min:12,temperature_max:24,rain_probability:10,wind_max:15});
  throw Error('Request inesperado: '+path);
};
window.addEventListener('load',async()=>{
  const checks=[];
  const assert=(value,label)=>{if(!value)throw Error(label);checks.push(label);};
  const wait=async predicate=>{for(let i=0;i<200;i++){if(predicate())return;await new Promise(r=>setTimeout(r,20));}throw Error('Timeout '+predicate);};
  const root=document.querySelector('[data-confirmed-layout]');
  const drawer=root.querySelector('[data-plan-drawer]');
  const control=name=>root.querySelector('[data-tool="'+name+'"]');
  const panel=name=>root.querySelector('[data-tool-panel="'+name+'"]');
  const postCount=()=>window.layoutCalls.filter(c=>c.path.endsWith('/recomendaciones/')&&c.method==='POST').length;
  const toolCount=()=>[...root.querySelectorAll('[data-tool-panel]')].filter(p=>!p.hidden).length;
  window.layoutCapture=stage=>{
    if(drawer.open)drawer.close();
    const active=root.querySelector('[data-tool][aria-expanded="true"]');
    if(active)active.click();
    if(stage==='drawer'){
      drawer.querySelectorAll('[data-decision-block]').forEach(node=>node.open=false);
      root.querySelector('[data-open-plan-details]').click();
      drawer.querySelector('.pb-plan-drawer-body').scrollTop=0;
    }
    else if(stage==='balances'){control('gastos').click();root.querySelector('.pb-expenses-people').scrollIntoView({block:'start'});return;}
    else if(['sugerencias','gastos','clima'].includes(stage))control(stage).click();
    if(['gastos','clima'].includes(stage))panel(stage).scrollIntoView({block:'start'});
    else window.scrollTo(0,0);
  };
  try{
    await wait(()=>window.layoutGenerated&&root.querySelectorAll('.pb-suggestion').length===5);
    assert(toolCount()===0,'Ninguna herramienta expandida inicialmente');
    assert(!drawer.open,'Drawer cerrado inicialmente');
    assert(root.querySelectorAll('[data-tool]').length===3,'Tres accesos compactos');
    const hero=root.querySelector('.pb-decision-panel');
    const heroWeather=hero.querySelector('[data-proposal-weather]');
    await wait(()=>!heroWeather.querySelector('[data-weather-details]').hidden);
    assert(hero.getBoundingClientRect().height<(innerWidth>900?600:1050),'Hero enriquecido de altura controlada');
    assert(root.getBoundingClientRect().height<1600,'Detalle inicial sin historia abierta');
    assert(hero.querySelector('.pb-confirmed-author .pb-user-avatar'),'Hero conserva avatar y autor');
    assert(heroWeather.textContent.includes('Parcialmente nublado')&&heroWeather.textContent.includes('Mín. 12 °C · Máx. 24 °C')&&heroWeather.textContent.includes('10%')&&heroWeather.textContent.includes('15 km/h'),'Hero muestra condición, temperaturas, lluvia y viento reales del endpoint');
    const heroMap=hero.querySelector('[data-proposal-preview]');
    assert(heroMap._leaflet_id&&heroMap.clientWidth>150&&heroMap.clientHeight>=190,'Mapa confirmado reutiliza preview Leaflet');
    const infoRect=hero.querySelector('.pb-confirmed-info').getBoundingClientRect();
    const mapRect=hero.querySelector('.pb-confirmed-location').getBoundingClientRect();
    assert(innerWidth>900?mapRect.left>infoRect.right-1:mapRect.top>=infoRect.bottom,'Hero en dos columnas desktop y mapa después del clima en mobile');
    assert(postCount()===1,'IA genera automáticamente una vez aun oculta');
    assert(root.querySelector('[data-ia-tool-status]').textContent.includes('5 lugares'),'Estado de IA sin endpoint adicional');
    assert(!window.layoutCalls.some(c=>c.path.endsWith('/gastos/')),'Gastos no se consulta para decorar tarjeta');
    const initialHeight=document.documentElement.scrollHeight;
    control('clima').click();
    assert(toolCount()===1&&!panel('clima').hidden,'Abre sólo clima');
    const province=root.querySelector('[data-weather-province]');
    province.value='buenos-aires';province.dispatchEvent(new Event('change'));
    const query=root.querySelector('[data-weather-query]');
    query.value='La Plata';query.dispatchEvent(new Event('input'));
    root.querySelector('[data-weather-search-form]').requestSubmit();
    await wait(()=>root.querySelector('[data-weather-cities] button'));
    root.querySelector('[data-weather-cities] button').click();
    await wait(()=>!root.querySelector('[data-weather-forecast]').hidden);
    assert(root.querySelectorAll('[data-weather-days] button').length===16,'Comparador carga pronóstico');
    const days=root.querySelector('[data-weather-days]');
    assert(getComputedStyle(days).scrollbarWidth==='none'&&getComputedStyle(days).overflowX==='auto','Carrusel oculta scrollbar y conserva overflow desplazable');
    assert(days.tabIndex===0&&days.scrollWidth>days.clientWidth,'Carrusel accesible por teclado con días fuera de vista');
    days.scrollLeft=300;await new Promise(r=>setTimeout(r,80));
    assert(days.scrollLeft>0,'Carrusel mantiene desplazamiento horizontal');
    days.querySelector('button:last-child').focus();
    assert(document.activeElement===days.lastElementChild,'Días conservan foco por teclado');
    root.querySelectorAll('[data-weather-days] button')[1].click();
    const weatherText=root.querySelector('[data-weather-selected]').textContent;
    control('gastos').click();
    await wait(()=>root.querySelector('[data-expense-summary]'));
    assert(toolCount()===1&&panel('clima').hidden,'Gastos reemplaza clima en el workspace');
    const expense=root.querySelector('[data-inline-expenses]');
    assert(expense.tagName==='SECTION'&&!expense.closest('dialog'),'Gastos completo inline, fuera del drawer');
    assert(expense.querySelector('table')&&expense.querySelector('[data-expense-form]'),'Balances y formulario real conservados');
    const metrics=[...expense.querySelectorAll('.pb-expenses-overview > div')].map(node=>node.getBoundingClientRect());
    assert(Math.abs(metrics[0].height-metrics[1].height)<2,'Resumen de gastos con bloques de igual altura');
    assert(getComputedStyle(expense.querySelector('tbody td')).textAlign==='right','Tabla de balances alinea cifras a la derecha');
    assert(expense.querySelector('.pb-transfer-route')&&expense.querySelector('.pb-transfer-amount'),'Para saldar separa recorrido y monto');
    const reminder=expense.querySelector('[data-debt-reminder-form]');
    assert(reminder&&reminder.querySelector('[name="csrfmiddlewaretoken"]')&&reminder.querySelector('[name="debtor_id"]'),'Notificar conserva permiso y campos existentes');
    expense.querySelector('[data-add-expense]').click();
    const form=expense.querySelector('[data-expense-form]');
    form.elements.description.value='Cena compartida';
    control('clima').click();
    assert(root.querySelector('[data-weather-selected]').textContent===weatherText,'Clima mantiene selección al alternar');
    control('gastos').click();
    assert(form.elements.description.value==='Cena compartida','Formulario conserva borrador al alternar');
    assert(window.layoutCalls.filter(c=>c.path.endsWith('/gastos/')&&c.method==='GET').length===1,'Reabrir gastos no duplica carga');
    assert(!expense.querySelector('[data-reload-expenses]').hidden,'Actualización manual de gastos siempre accesible');
    form.elements.amount.value='1000';form.elements.paid_by.value=form.elements.paid_by.options[1].value;
    form.elements.all_group.checked=true;
    form.elements.all_group.dispatchEvent(new Event('change',{bubbles:true}));
    form.requestSubmit();
    await wait(()=>window.layoutCalls.some(c=>c.path.endsWith('/gastos/')&&c.method==='POST'));
    await wait(()=>expense.querySelector('[data-expenses-status]').textContent.includes('cuentas están actualizadas'));
    assert(window.layoutCalls.find(c=>c.path.endsWith('/gastos/')&&c.method==='POST').fields.includes('csrfmiddlewaretoken'),'Gasto conserva formulario y CSRF');
    control('sugerencias').click();
    await new Promise(r=>setTimeout(r,80));
    const map=root.querySelector('[data-suggestions-map]');
    const mapId=map._leaflet_id;
    assert(map.clientWidth>150&&map.clientHeight>=200,'Mapa oculto recupera dimensiones al abrir');
    assert(root.querySelectorAll('.pb-suggestion').length===5,'Cards y summary conservados');
    root.querySelector('[data-select-place="1"]').click();
    assert(root.querySelector('[data-select-place="1"]').getAttribute('aria-pressed')==='true','Card selecciona marker');
    const pins=[...map.querySelectorAll('.pb-suggestion-marker')];pins[2].click();
    assert(root.querySelector('[data-select-place="2"]').getAttribute('aria-pressed')==='true','Marker selecciona card');
    control('sugerencias').click();assert(toolCount()===0,'Seleccionar herramienta activa la cierra');
    control('sugerencias').click();await new Promise(r=>setTimeout(r,40));
    assert(map._leaflet_id===mapId&&postCount()===1,'Reapertura no recrea mapa ni genera recomendaciones');
    const opener=root.querySelector('[data-open-plan-details]');opener.click();
    assert(drawer.open&&drawer.matches(':modal'),'Drawer modal con fondo inerte nativo');
    assert(document.activeElement===drawer.querySelector('[data-close-plan-details]'),'Foco inicial en cerrar');
    assert(drawer.querySelectorAll('[data-decision-block]').length===4,'Cuatro acordeones de historia');
    assert(![...drawer.querySelectorAll('[data-decision-block]')].some(d=>d.open),'Historia colapsada por defecto');
    assert(!drawer.querySelector('[data-plan-weather],[data-recommendations],[data-expenses-dialog]'),'Herramientas activas fuera del drawer');
    if(innerWidth<=600)assert(Math.abs(drawer.getBoundingClientRect().width-innerWidth)<2,'Drawer mobile fullscreen');
    else assert(drawer.getBoundingClientRect().width>=440&&drawer.getBoundingClientRect().width<=560,'Drawer desktop de ancho contextual');
    const first=drawer.querySelector('[data-close-plan-details]');first.focus();
    first.dispatchEvent(new KeyboardEvent('keydown',{key:'Tab',shiftKey:true,bubbles:true,cancelable:true}));
    assert(drawer.contains(document.activeElement)&&document.activeElement!==first,'Focus trap Shift Tab');
    document.activeElement.dispatchEvent(new KeyboardEvent('keydown',{key:'Tab',bubbles:true,cancelable:true}));
    assert(document.activeElement===first,'Focus trap Tab vuelve al inicio');
    drawer.querySelector('[data-decision-block="proposals"]').open=true;
    await new Promise(r=>setTimeout(r,50));
    assert(drawer.querySelectorAll('.pb-proposal-card').length===2&&drawer.querySelectorAll('.pb-confirmed-history-reference').length===1,'Historial conserva alternativas y referencia compacta de la elegida');
    assert(drawer.querySelector('[data-proposal-preview]').clientWidth>150,'Preview de propuesta recupera tamaño');
    assert(drawer.querySelector('.pb-plan-drawer-body').scrollWidth<=drawer.querySelector('.pb-plan-drawer-body').clientWidth+1,'Drawer sin scroll horizontal con propuestas abiertas');
    drawer.querySelector('[data-decision-block="method"]').open=true;
    drawer.querySelector('[data-decision-block="voting"]').open=true;
    assert(drawer.querySelector('#proposal-election-title')&&drawer.querySelector('#proposal-vote-title'),'Método y votación siguen accesibles');
    first.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true,cancelable:true}));
    await wait(()=>!drawer.open&&document.activeElement===opener&&opener.getAttribute('aria-expanded')==='false');
    assert(document.activeElement===opener&&opener.getAttribute('aria-expanded')==='false','Escape cierra y devuelve foco');
    root.querySelector('[data-decision-target]').click();
    assert(drawer.open&&drawer.querySelector('[data-decision-block="result"]').open,'Hero abre proceso en resultado');
    assert(document.activeElement.id==='decision-history-title','Proceso recibe el foco');
    drawer.querySelector('[data-close-plan-details]').click();await new Promise(r=>setTimeout(r,20));
    assert(!drawer.open,'Botón X cierra drawer');
    opener.click();const rect=drawer.getBoundingClientRect();
    drawer.dispatchEvent(new MouseEvent('click',{clientX:rect.left-1,clientY:20,bubbles:true}));
    assert(!drawer.open,'Click en backdrop cierra drawer');
    history.replaceState(null,'',location.pathname+'#clima');window.dispatchEvent(new HashChangeEvent('hashchange'));
    assert(!panel('clima').hidden,'Hash abre clima');
    history.replaceState(null,'',location.pathname+'#decision');window.dispatchEvent(new HashChangeEvent('hashchange'));
    assert(drawer.open,'Hash abre decisión');drawer.close();
    history.replaceState(null,'',location.pathname);window.layoutCapture('compact');
    assert(document.documentElement.scrollHeight<=initialHeight+2,'Cerrar herramientas vuelve a la página compacta');
    assert(document.documentElement.scrollWidth<=innerWidth+1,'Sin scroll horizontal');
    assert(postCount()===1,'Toda la navegación conserva una generación efectiva');
    assert(window.layoutCalls.filter(c=>c.path===heroWeather.dataset.weatherUrl).length===1,'Clima elegido se consulta una vez sin duplicación en drawer');
    assert(heroMap._leaflet_id&&root.querySelectorAll('.pb-confirmed-location [data-proposal-preview]').length===1,'Mapa elegido conserva instancia única');
    window.layoutCapture('sugerencias');
    const result=document.createElement('pre');result.id='browser-results';result.textContent=JSON.stringify({ok:true,width:innerWidth,checks});document.body.append(result);
  }catch(error){const result=document.createElement('pre');result.id='browser-results';result.textContent=JSON.stringify({ok:false,width:innerWidth,checks,error:error.message});document.body.append(result);}
});
</script>
"""


def main():
    summary = summarize(snapshot(expense()), USER_ID)
    summary['transfers'][0]['can_notify'] = True
    panel = render_to_string('plans/expenses/panel.html', {
        'summary': summary, 'form': ExpenseForm(members=summary['members']),
        'plan': confirmed_plan(), 'csrf_token': 'fixture-csrf-token',
    })
    browser.HARNESS = HARNESS.replace('__EXPENSE_PANEL__', json.dumps(panel))
    proposals = [confirmed_proposal()]
    proposals[0].update(description='Un encuentro para compartir con el grupo.', budget_min=1000, budget_max=2000, votes=3)
    for index in (1, 2):
        proposal = confirmed_proposal()
        proposal.update(id=47+index, tittle=f'Alternativa {index}', created_by=f'00000000-0000-0000-0000-{index:012d}', votes=index)
        proposals.append(proposal)
    browser.main(proposals=proposals, plan_fixture=confirmed_plan(election_method='votes'), group_role='owner', all_scripts=True)


if __name__ == '__main__':
    main()
