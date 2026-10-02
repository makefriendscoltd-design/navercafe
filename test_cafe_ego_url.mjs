import assert from 'node:assert/strict';
import {publishedLocationReady as ready} from './cafe_ego_url.mjs';
const encoded='https://cafe.naver.com/westudyssat?iframe_url_utf8=%2FArticleRead.nhn%253Fclubid%3D26321967%2526articleid%3D6081%2526menuid%3D163';
assert.equal(ready({url:encoded,editorPresent:false}),true);
assert.equal(ready({url:encoded,editorPresent:true}),false);
assert.equal(ready({url:'https://cafe.naver.com/westudyssat',editorPresent:false}),false);
assert.equal(ready({url:'https://cafe.naver.com/ca-fe/cafes/26321967/menus/163/articles/write',editorPresent:true}),false);
assert.equal(ready({url:'https://cafe.naver.com/ca-fe/cafes/26321967/articles/6246',editorPresent:false}),true);
console.log('5 publication redirect checks passed');
