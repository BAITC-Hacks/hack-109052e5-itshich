import {sourceRows} from './source-data.mjs';
const title=s=>s.charAt(0).toUpperCase()+s.slice(1);
const list=s=>s?s.split('|').map(s=>s.trim()).filter(Boolean):[];
const flag=s=>s==='True';
export const data=sourceRows.map(row=>({
 id:row.id,name:row.anon_name,
 initials:row.anon_name.split(/\s+/).slice(0,2).map(s=>s[0]).join(''),
 categories:list(row.categories),city:row.city,
 price:Number(row.price_from_kzt),formats:list(row.event_formats).map(title),
 languages:list(row.languages).map(title),hours:row.max_hours===''?null:Number(row.max_hours),
 busy:list(row.busy_dates),detail:row.description,
 synthetic:flag(row.synthetic),cityImputed:flag(row.city_imputed),priceImputed:flag(row.price_imputed)
}));
export const options={cities:[...new Set(data.map(p=>p.city))],categories:[...new Set(data.flatMap(p=>p.categories))].sort((a,b)=>a.localeCompare(b,'ru')),formats:[...new Set(data.flatMap(p=>p.formats))],languages:[...new Set(data.flatMap(p=>p.languages))]};
export const scenarios={
 popular:{city:'Алматы',category:'Ведущий',format:'Свадьба',date:'2026-11-14',budget:1000000,hours:'',language:''},
 rare:{city:'Алматы',category:'Флорист',format:'Свадьба',date:'2026-11-14',budget:300000,hours:'',language:''},
 absent:{city:'Астана',category:'Декоратор',format:'Свадьба',date:'2026-11-14',budget:3000000,hours:'',language:''},
 empty:{city:'Алматы',category:'Флорист',format:'Свадьба',date:'2026-12-03',budget:300000,hours:'',language:''},
 date:{city:'Алматы',category:'Ведущий',format:'Свадьба',date:'2026-11-15',budget:1000000,hours:'',language:''}
};
export function descriptionExcerpt(p){
 const text=p.detail.replace(/\s+/g,' ').trim();
 const sentences=text.match(/[^.!?]+(?:[.!?]+|$)/g)||[text];
 const score=s=>(/стиль|сценар|специал|репертуар|подача|предлага|снима|оформ|цвет|оборуд|вместим|монтаж/i.test(s)?3:0)-(/меня зовут|привет|востребован|лучший|статистика/i.test(s)?4:0);
 const candidates=sentences.filter(s=>s.trim().length>=35&&s.trim().length<=300).sort((a,b)=>score(b)-score(a));
 const useful=candidates[0]||text.split('Статистика:')[0]||text;
 const clean=useful.split('Статистика:')[0].trim();
 if(clean.length<=240)return clean;
 return clean.slice(0,237).replace(/\s+\S*$/,'')+'…';
}
export function explanation(p,q){
 const money=n=>new Intl.NumberFormat('ru-RU').format(n);
 const terms=[`Формат «${q.format.toLowerCase()}» указан в профиле`,q.budget===p.price?'стартовая цена равна бюджету':`стартовая цена на ${money(q.budget-p.price)} ₸ ниже бюджета`];
 if(q.language)terms.push(`язык — ${q.language.toLowerCase()}`);
 if(q.hours)terms.push(p.hours===null?'работа не привязана к часам на площадке':`длительность ${q.hours} ч укладывается в лимит ${p.hours} ч`);
 return `${terms.join('; ')}. Из описания: «${descriptionExcerpt(p)}»`;
}
