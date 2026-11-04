'use strict';
if(typeof SwaggerUIBundle!=='function'){
  document.getElementById('swagger-ui').textContent='Swagger UI 파일을 불러오지 못했습니다. 서버 상태와 /docs/swagger-ui-bundle.js 응답을 확인하세요.';
}else{
  window.ui=SwaggerUIBundle({
    url:'/openapi.json',
    dom_id:'#swagger-ui',
    presets:[SwaggerUIBundle.presets.apis],
    layout:'BaseLayout',
    deepLinking:true,
    docExpansion:'list',
    defaultModelsExpandDepth:0,
    displayRequestDuration:true,
    tryItOutEnabled:false,
    validatorUrl:null,
    supportedSubmitMethods:['get','post'],
    requestInterceptor(request){
      const target=new URL(request.url,window.location.href);
      if(target.origin!==window.location.origin)throw Error('현재 로컬 서버의 API만 호출할 수 있습니다.');
      return request;
    },
    responseInterceptor(response){
      if(new URL(response.url,window.location.href).pathname==='/api/status'&&response.status===200){
        try{
          const state=typeof response.body==='string'?JSON.parse(response.body):response.body;
          document.getElementById('docs-status').textContent=`API 연결 확인 · ${state.api_version} · 계약 ${state.contracts.length}건 · 항차 ${state.calls.length}건 · 계획 기준 ${state.as_of||'입력 대기'}`;
        }catch(_){}
      }
      return response;
    },
    onFailure(){document.getElementById('docs-status').textContent='명세를 불러오지 못했습니다. /openapi.json 응답을 확인하세요.';}
  });
}
