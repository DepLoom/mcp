// Pinned host TypeScript parses strings. Project files/modules are never executed.
'use strict';
const fs=require('node:fs');
const ts=require(process.argv[2]);
const input=process.argv[3];
if(fs.statSync(input).size>2*1024*1024)throw Error('USAGE_INPUT_TOO_LARGE');
const payload=JSON.parse(fs.readFileSync(input,'utf8'));
if(!Array.isArray(payload.files)||payload.files.length>256||!Array.isArray(payload.packages))throw Error('INVALID_USAGE_INPUT');
const targets=new Set(payload.packages),observations=[];
let parseErrors=0,dynamicLoads=0,aliasLoads=0,dynamicConfigs=0;
function nameOf(node){return ts.isIdentifier(node)||ts.isStringLiteral(node)?node.text:null;}
function propertyPath(node){
 const parts=[];let current=node;
 while(current&&!ts.isSourceFile(current)){
  if(ts.isPropertyAssignment(current)){const part=nameOf(current.name);if(part===null)return null;parts.unshift(part);}
  if(ts.isCallExpression(current))return {parts,call:current};
  if(ts.isArrowFunction(current)||ts.isFunctionExpression(current))return null;
  current=current.parent;
 }
 return null;
}
for(const file of payload.files){
 const ext=file.path.toLowerCase();
 const kind=ext.endsWith('.tsx')?ts.ScriptKind.TSX:ext.endsWith('.jsx')?ts.ScriptKind.JSX:/\.(?:[cm]?ts)$/.test(ext)?ts.ScriptKind.TS:ts.ScriptKind.JS;
 const source=ts.createSourceFile(file.path,file.text,ts.ScriptTarget.Latest,true,kind);
 if(source.parseDiagnostics.length){parseErrors+=source.parseDiagnostics.length;continue;}
 const viteConfigNames=new Set();
 function add(node,packageName,form,extra={}){
  const position=source.getLineAndCharacterOfPosition(node.getStart(source));
  observations.push({path:file.path,line:position.line+1,column:position.character+1,package:packageName,form,
   authority:'OBSERVED_SYNTAX_ONLY',semanticApplicability:'UNKNOWN',...extra});
 }
 for(const statement of source.statements){
  if(ts.isImportDeclaration(statement)&&ts.isStringLiteral(statement.moduleSpecifier)&&statement.moduleSpecifier.text==='vite'){
   const bindings=statement.importClause?.namedBindings;
   if(bindings&&ts.isNamedImports(bindings))for(const element of bindings.elements)
    if((element.propertyName||element.name).text==='defineConfig')viteConfigNames.add(element.name.text);
  }
 }
 function visit(node){
  if(ts.isImportDeclaration(node)&&ts.isStringLiteral(node.moduleSpecifier)&&targets.has(node.moduleSpecifier.text)){
   const bindings=node.importClause?.namedBindings;
   const names=bindings&&ts.isNamedImports(bindings)?bindings.elements.map(e=>(e.propertyName||e.name).text).filter(x=>['defineConfig','splitVendorChunkPlugin'].includes(x)):[];
   add(node,node.moduleSpecifier.text,node.importClause?.name?'DEFAULT_IMPORT':'MODULE_IMPORT',{typeOnly:!!node.importClause?.isTypeOnly,knownImportedSymbols:names,executionOrUseProven:false});
  }
  if(ts.isImportEqualsDeclaration(node)&&ts.isExternalModuleReference(node.moduleReference)&&node.moduleReference.expression&&ts.isStringLiteral(node.moduleReference.expression)&&targets.has(node.moduleReference.expression.text))
   add(node,node.moduleReference.expression.text,'IMPORT_EQUALS_REQUIRE',{executionOrUseProven:false});
  if(ts.isCallExpression(node)){
   const isRequire=ts.isIdentifier(node.expression)&&node.expression.text==='require';
   const isImport=node.expression.kind===ts.SyntaxKind.ImportKeyword;
   if(isRequire||isImport){
    const argument=node.arguments[0];
    if(node.arguments.length!==1||!argument||!ts.isStringLiteral(argument))dynamicLoads++;
    else if(targets.has(argument.text)){
     if(isImport)dynamicLoads++;
     add(node,argument.text,isImport?'DYNAMIC_IMPORT_LITERAL':'REQUIRE_LITERAL',{bindingOrRuntimeResolution:'UNKNOWN'});
    }
   }
   if(ts.isIdentifier(node.expression)&&viteConfigNames.has(node.expression.text)&&targets.has('vite')){
    const argument=node.arguments[0];if(!argument||!ts.isObjectLiteralExpression(argument))dynamicConfigs++;
    else if(argument.properties.some(p=>ts.isSpreadAssignment(p)))dynamicConfigs++;
   }
  }
  if(targets.has('vite')&&(ts.isSpreadAssignment(node)||ts.isComputedPropertyName(node))){
   let parent=node.parent;
   while(parent&&!ts.isSourceFile(parent)){
    if(ts.isCallExpression(parent)&&ts.isIdentifier(parent.expression)&&viteConfigNames.has(parent.expression.text)){dynamicConfigs++;break;}
    parent=parent.parent;
   }
  }
  if(ts.isPropertyAssignment(node)&&targets.has('vite')){
   const context=propertyPath(node);
   if(context&&ts.isIdentifier(context.call.expression)&&viteConfigNames.has(context.call.expression.text)
     &&context.parts[0]==='css'&&(ts.isIdentifier(node.initializer)||ts.isCallExpression(node.initializer)||ts.isPropertyAccessExpression(node.initializer)))dynamicConfigs++;
  }
  if(ts.isVariableDeclaration(node)&&node.initializer&&ts.isIdentifier(node.initializer)&&node.initializer.text==='require')aliasLoads++;
  if(ts.isPropertyAssignment(node)&&nameOf(node.name)==='api'&&ts.isStringLiteral(node.initializer)&&targets.has('vite')){
   const context=propertyPath(node);
   if(context&&ts.isIdentifier(context.call.expression)&&viteConfigNames.has(context.call.expression.text)
     &&['css.preprocessorOptions.scss.api','css.preprocessorOptions.sass.api'].includes(context.parts.join('.'))){
    const allowed=['legacy','modern','modern-compiler'];
    add(node,'vite','VITE_SASS_API_LITERAL',{value:allowed.includes(node.initializer.text)?node.initializer.text:'OTHER_LITERAL',configEvaluation:'NOT_PERFORMED'});
   }
  }
  ts.forEachChild(node,visit);
 }
 visit(source);
}
const cpu=process.cpuUsage();
process.stdout.write(JSON.stringify({parserVersion:ts.version,parserReportedCpuSeconds:(cpu.user+cpu.system)/1000000,observations,parseErrors,dynamicLoads,aliasLoads,dynamicConfigs,
 sourceEvaluationPerformed:false,projectImportsResolved:false}));
