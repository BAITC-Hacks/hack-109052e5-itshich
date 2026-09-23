export function selectProfiles(data,q){
 const pool=data.filter(p=>p.city===q.city&&(p.categories??[p.category]).includes(q.category));
 const reasons={busy:0,budget:0,format:0,language:0,hours:0};
 const eligible=pool.filter(p=>{const fails={busy:p.busy.includes(q.date),budget:p.price>q.budget,format:!p.formats.includes(q.format),language:!!q.language&&!p.languages.includes(q.language),hours:!!q.hours&&p.hours!==null&&p.hours<q.hours}; for(const k in fails)if(fails[k])reasons[k]++;return !Object.values(fails).some(Boolean);});
 eligible.sort((a,b)=>a.price-b.price||String(a.id).localeCompare(String(b.id)));
 return {state:!pool.length?'absent':!eligible.length?'filtered':'success',items:eligible.slice(0,3),total:eligible.length,pool:pool.length,reasons};
}
