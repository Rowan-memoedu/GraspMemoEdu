import assert from 'node:assert/strict';
import {mathPalette, subjectPalette, subjectHues} from '../web/subjects.js';
import {setLanguage, learningTitle, t} from '../web/i18n.js';
const luminance=hex=>[1,3,5].reduce((sum,i,n)=>{
 const c=parseInt(hex.slice(i,i+2),16)/255;
 return sum+(c<=.04045?c/12.92:((c+.055)/1.055)**2.4)*[.2126,.7152,.0722][n];
},0);
assert.deepEqual(subjectPalette('math'),mathPalette);
for(const id of Object.keys(subjectHues)){
 const palette=subjectPalette(id);
 for(const [key,base] of Object.entries(mathPalette)){
   if(id!=='physics')assert.ok(luminance(palette[key])<=luminance(base)+.006,`${id}: ${key} lost contrast`);
   if(id==='math'||id==='english'||luminance(base)>.5)assert.ok(Math.abs(luminance(palette[key])-luminance(base))<.006,`${id}: ${key} changed reference brightness`);
   if(id==='chemistry')assert.equal(new Set(palette[key].slice(1).match(/../g)).size,1);
 }
 const contrast=id==='physics'?(luminance(palette['--link-color'])+.05)/.05:1.05/(luminance(palette['--link-color'])+.05);
 assert.ok(contrast>=4.5,`${id}: button contrast`);
 assert.ok(1.05/(luminance(palette['--ma-navy'])+.05)>=14,`${id}: heading contrast`);
 console.log(id, palette['--ma-navy'],palette['--link-color']);
}
assert.equal(subjectPalette('chemistry')['--link-color'],'#111111');
assert.equal(subjectPalette('physics')['--link-color'],'#E67E00');
for(const id of ['biology','chinese'])assert.ok(luminance(subjectPalette(id)['--link-color'])<luminance(mathPalette['--link-color'])*.8);
globalThis.document={documentElement:{}};
setLanguage('zh-CN');assert.equal(t('Introduction'),'课程介绍');
assert.deepEqual(['again','hard','good','easy'].map(key=>t('review.'+key)),['需再练习','较难','良好','轻松']);
assert.equal(learningTitle('Example 1: 原样标题'),'例题 1: 原样标题');
assert.equal(learningTitle('Practice 2.1： Original title'),'练习 2.1： Original title');
assert.equal(learningTitle('Example in the content'),'Example in the content');
setLanguage('en');assert.equal(t('Introduction'),'Introduction');
assert.equal(learningTitle('例题 1: 原样标题'),'Example 1: 原样标题');
console.log('PASS shared reference palette, stronger accents, black chemistry buttons and UI-only terminology');
