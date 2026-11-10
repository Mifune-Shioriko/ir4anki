// Actual Solid universal renderer; in-memory host replaces only browser DOM.
// Production component bodies, Show, effects, cleanup, props and writers run unchanged.
import {createRenderer} from 'solid-js/universal'
export const root=()=>({tag:'root',children:[],props:{}})
export const {render,effect,memo,createComponent,createElement,createTextNode,insertNode,insert,spread,setProp,mergeProps,use}=createRenderer({
 createElement:tag=>({tag,children:[],props:{}}),createTextNode:text=>({text,children:[],props:{}}),
 replaceText:(n,text)=>{n.text=text},isTextNode:n=>'text' in n,
 setProperty:(n,k,v)=>{n.props[k]=v},
 insertNode:(p,n,a)=>{if(n.parent){const i=n.parent.children.indexOf(n);if(i>=0)n.parent.children.splice(i,1)}n.parent=p;const i=a?p.children.indexOf(a):-1;p.children.splice(i<0?p.children.length:i,0,n)},
 removeNode:(p,n)=>{const i=p.children.indexOf(n);if(i>=0)p.children.splice(i,1);n.parent=undefined},
 getParentNode:n=>n.parent,getFirstChild:n=>n.children[0],getNextSibling:n=>n.parent?.children[n.parent.children.indexOf(n)+1],
})
export const find=(n,test)=>test(n)?n:n.children.map(x=>find(x,test)).find(Boolean)
