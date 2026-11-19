/* Per-dialog identity. Looking at an audit is a read, never a change of review selection. */
"use strict";
function createCaseReader({read, loading, render, failure}) {
  let id = null, serial = 0;
  async function open(caseId, source, refresh=false) {
    if (!caseId) return;
    const n=++serial;
    id=caseId; loading(caseId,source,refresh);
    try {
      const detail=await read(caseId);
      if(n!==serial) return;
      if(detail.id!==caseId) throw Error("The server returned a different case.");
      render(detail,refresh);
    } catch(e) {if(n===serial) failure(e);}
  }
  return {open,refresh:()=>open(id,null,true),close:()=>{++serial;id=null;},id:()=>id};
}
if(typeof module!=="undefined" && module.exports) module.exports={createCaseReader};
