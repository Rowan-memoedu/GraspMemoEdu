import fs from 'node:fs';
import assert from 'node:assert/strict';

export const calloutConfigFile=new URL('../config/callouts.json',import.meta.url);
const paletteKeys=['background','accent','text','link'];
const ownKeys=(value,keys,label)=>{
  assert.ok(value&&typeof value==='object'&&!Array.isArray(value),label+' must be an object');
  assert.deepEqual(Object.keys(value).sort(),[...keys].sort(),'Unknown or missing '+label+' field');
};
export function validateCalloutConfig(config){
  ownKeys(config,['schema','layout','annotations','draft','tags'],'callout config');
  assert.equal(config.schema,'website-callouts-v1','Unsupported callout configuration');
  ownKeys(config.layout,['focusedWidthPercent','expandedWidthPercent','maxHeightPx','marginBlockEm','paddingEm'],'layout');
  const bounds={focusedWidthPercent:[10,100],expandedWidthPercent:[10,100],maxHeightPx:[80,1600],marginBlockEm:[0,8],paddingEm:[0.25,4]};
  for(const [key,[min,max]] of Object.entries(bounds))assert.ok(typeof config.layout[key]==='number'&&Number.isFinite(config.layout[key])&&config.layout[key]>=min&&config.layout[key]<=max,'Invalid callout '+key);
  const palette=(value,label)=>{
    for(const key of paletteKeys)assert.match(value[key]??'',/^#[0-9a-f]{6}$/i,'Invalid '+label+' '+key+' color');
  };
  for(const key of ['annotations','draft']){ownKeys(config[key],paletteKeys,key);palette(config[key],key);}
  assert.ok(Array.isArray(config.tags),'Callout tags must be an array');
  const keys=new Set(['note','danger']),names=new Set();
  for(const tag of config.tags){
    ownKeys(tag,['key','name',...paletteKeys],'tag');
    assert.match(tag.key??'',/^[a-z][a-z0-9-]{0,40}$/,'Invalid callout key');
    assert.ok(!keys.has(tag.key),'Duplicate or reserved callout key');keys.add(tag.key);
    assert.ok(typeof tag.name==='string'&&tag.name===tag.name.trim()&&tag.name.length>0&&tag.name.length<=80,'Invalid callout tag name');
    const name=tag.name.toLowerCase();assert.ok(!names.has(name),'Duplicate callout tag name');names.add(name);
    palette(tag,tag.key);
  }
  return config;
}
export function loadCalloutConfig(file=calloutConfigFile){
  return validateCalloutConfig(JSON.parse(fs.readFileSync(file,'utf8').replace(/^\uFEFF/,'')));
}
