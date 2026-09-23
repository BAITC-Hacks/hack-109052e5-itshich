// Render snippets as text nodes; user code is never inserted as HTML or executed.
function highlight(code, language) {
 const source=code.textContent;
 code.replaceChildren();
 if(language==='Текст'){code.textContent=source;return;}
 const tokens=/(\/\/[^\n]*|\/\*[\s\S]*?\*\/)|("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`)|\b(import|from|const|let|return|export|function|if|else|await|async|true|false|null)\b|\b(\d[\d_]*(?:\.\d+)?)\b/g;
 let end=0;
 for(const match of source.matchAll(tokens)){
  code.append(document.createTextNode(source.slice(end,match.index)));
  const token=document.createElement('span');
  token.className='token-'+(match[1]?'comment':match[2]?'string':match[3]?'keyword':'number');
  token.textContent=match[0];code.append(token);end=match.index+match[0].length;
 }
 code.append(document.createTextNode(source.slice(end)));
}
document.querySelectorAll('pre code').forEach(code=>highlight(code,code.dataset.language));
const input=document.querySelector('#snippet-input'),language=document.querySelector('#snippet-language'),status=document.querySelector('#format-status');
const preview=document.querySelector('#formatting .code-block');
function applyFormatting(){
 let source=input.value;
 try{
  if(language.value==='JSON')source=JSON.stringify(JSON.parse(source),null,2);
  const code=preview.querySelector('code');code.textContent=source;code.dataset.language=language.value;highlight(code,language.value);
  preview.querySelector('.language').textContent=language.value;
  input.value=source;input.removeAttribute('aria-invalid');status.className='';
  status.textContent=language.value==='JSON'?'JSON отформатирован. Предпросмотр обновлён.':language.value==='JavaScript'?'JavaScript подсвечен, исходные отступы сохранены.':'Показано как текст, без подсветки.';
  return true;
 }catch{
  input.setAttribute('aria-invalid','true');status.className='error';status.textContent='Не удалось разобрать JSON. Проверьте кавычки, запятые и скобки. Предыдущий предпросмотр сохранён.';
  return false;
 }
}
document.querySelector('#format-code').addEventListener('click',applyFormatting);
input.setAttribute('aria-describedby','format-status');
// Formatting follows the selected language immediately; the button re-applies it after edits.
language.addEventListener('change',applyFormatting);
document.querySelectorAll('.copy-code').forEach(button=>button.addEventListener('click',async()=>{
 const code=button.closest('.code-block').querySelector('code');
 try{
  await navigator.clipboard.writeText(code.textContent);
  button.textContent='Скопировано';document.querySelector('#copy-status').textContent='Код скопирован в буфер обмена.';
  setTimeout(()=>{button.textContent='Копировать';},1800);
 }catch{
  const range=document.createRange();range.selectNodeContents(code);const selection=window.getSelection();selection.removeAllRanges();selection.addRange(range);
  button.textContent='Код выделен';document.querySelector('#copy-status').textContent='Автоматическое копирование недоступно. Нажмите Ctrl+C или Command+C.';
 }
}));
const sections=[...document.querySelectorAll('article section')];
function markSection(){
 const current=sections.filter(section=>section.getBoundingClientRect().top<=160).at(-1)||sections[0];
 document.querySelectorAll('a[href^="#"]').forEach(link=>{if(!link.closest('nav'))return;if(link.hash==='#'+current.id)link.setAttribute('aria-current','location');else link.removeAttribute('aria-current');});
}
let scheduled=false;window.addEventListener('scroll',()=>{if(scheduled)return;scheduled=true;requestAnimationFrame(()=>{markSection();scheduled=false;});},{passive:true});markSection();
document.querySelectorAll('.mobile-nav a').forEach(link=>link.addEventListener('click',()=>{document.querySelector('.mobile-nav').open=false;}));
