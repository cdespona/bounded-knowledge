"""Bounded, positioned-tree Terraform and Terragrunt declaration inventory."""
import os
from pathlib import Path

import hcl2
from lark import Tree

from landscape_core.contracts import TERRAFORM_GAP_DETAILS
from landscape_core.observations import observation
from landscape_core.safety import MAX_FILE_BYTES, directory_exclusion_reason, exclusion_reason

MAX_FILES, MAX_DEPTH, MAX_NODES, MAX_BLOCKS, MAX_ATTRIBUTES, MAX_REFERENCES = 4096, 64, 50000, 4096, 16384, 50000


def _text(node):
    return "".join(str(x) for x in node.scan_values(lambda _: True)) if isinstance(node, Tree) else str(node)


def _identifier(node):
    return str(node.children[0]) if isinstance(node, Tree) and node.data == "identifier" else ""


def _literal_string(node):
    if not isinstance(node, Tree) or node.data != "string": return None
    value = "".join(str(x) for x in node.scan_values(lambda x: getattr(x, "type", "") == "STRING_CHARS"))
    return value if "${" not in value and "%{" not in value else None


def _traversal(node):
    if not isinstance(node, Tree): return None
    if node.data == "identifier": return _identifier(node)
    if node.data == "expr_term" and node.children: return _traversal(node.children[0])
    if node.data == "get_attr_expr_term" and len(node.children) == 2:
        base = _traversal(node.children[0]); attribute = node.children[1]
        name = _identifier(attribute.children[-1]) if isinstance(attribute, Tree) else ""
        return base + "." + name if base and name else None
    return None


def _safe_line_span(text, start, end):
    """A raw range is selectable only when it is the entire physical line."""
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    if line_end < 0:
        line_end = len(text)
    return bool(text[start:end].strip()) and text[line_start:line_end].strip() == text[start:end].strip()


def _block_header_end(block):
    return next((child.end_pos for child in block.children if str(child) == "{"), block.meta.start_pos)


class TerraformDetector:
    name, version = "terraform", 1
    def __init__(self, source_selection): self.selection = dict(source_selection)
    def _item(self, root, path, repository, commit, line, kind, value):
        if kind.startswith("terraform-") and kind != "terraform-gap":
            value = dict(value)
            value.setdefault("evidenceDisposition", "inventory-only")
        return observation(kind=kind, repository=repository, commit=commit, detector=self.name, detector_version=self.version, source_path=path.relative_to(root).as_posix(), source_lines=str(max(1, line)), value=value)
    def _gap(self, root,path,repository,commit,line,code,context="terraform",form="hcl"):
        return self._item(root,path,repository,commit,line,"terraform-gap", {"context":context,"form":form,"code":code,"detail":TERRAFORM_GAP_DETAILS[code],"sourceSelectionId":self.selection["id"]})
    def _files(self, root, selected):
        files, excluded = [], []
        for current, dirs, names in os.walk(selected, topdown=True):
            here=Path(current); keep=[]
            for name in sorted(dirs):
                path=here/name; rel=path.relative_to(root); reason=directory_exclusion_reason(rel)
                if path.is_symlink(): excluded.append({"path":rel.as_posix(),"reason":"symbolic-link"})
                elif reason: excluded.append({"path":rel.as_posix(),"reason":reason})
                else: keep.append(name)
            dirs[:]=keep
            for name in sorted(names):
                path=here/name; rel=path.relative_to(root); lower=name.lower()
                if path.is_symlink(): excluded.append({"path":rel.as_posix(),"reason":"symbolic-link"}); continue
                if lower.endswith(".tfvars") or lower.endswith(".tfvars.json") or lower.endswith(".tfstate") or ".tfstate." in lower or lower.endswith(".tfplan"): continue
                if name.endswith(".tf.json"):
                    files.append((path,"json")); continue
                if name.endswith(".tf") or name == "terragrunt.hcl": files.append((path,"hcl"))
        files.sort(key=lambda x:x[0].relative_to(root).as_posix()); return files, sorted(excluded,key=lambda x:(x["path"],x["reason"]))
    def detect(self, root, repository, commit):
        selected=root/self.selection["subpath"]; files, excluded=self._files(root,selected); result=[]
        for index,(path,kind) in enumerate(files):
            if index >= MAX_FILES: excluded.append({"path":path.relative_to(root).as_posix(),"reason":"selection-file-limit"}); continue
            try:
                reason = exclusion_reason(path.relative_to(root), path.stat().st_size)
            except OSError:
                excluded.append({"path":path.relative_to(root).as_posix(),"reason":"unreadable-file"}); continue
            if reason:
                excluded.append({"path":path.relative_to(root).as_posix(),"reason":reason}); continue
            if kind == "json": result.append(self._gap(root,path,repository,commit,1,"unsupported-terraform-json")); continue
            try:
                raw=path.read_bytes()
                text=raw.decode("utf-8"); tree=hcl2.parses_to_tree(text)
                nodes=list(tree.iter_subtrees_topdown())
                if len(nodes)>MAX_NODES or any(n.meta.end_line-n.meta.line+1>MAX_DEPTH for n in nodes): raise ValueError()
            except (OSError,UnicodeError): excluded.append({"path":path.relative_to(root).as_posix(),"reason":"unsupported-or-unreadable-content"}); continue
            except Exception: result.append(self._gap(root,path,repository,commit,1,"malformed-hcl")); continue
            blocks=[n for n in nodes if n.data=="block"]
            attrs=[n for n in nodes if n.data=="attribute"]
            if len(blocks)>MAX_BLOCKS or len(attrs)>MAX_ATTRIBUTES: result.append(self._gap(root,path,repository,commit,1,"terraform-parser-resource-limit")); continue
            terragrunt=path.name=="terragrunt.hcl"
            for block in blocks:
                children=block.children; name=_identifier(children[0]); labels=[_literal_string(x) for x in children[1:] if isinstance(x,Tree) and x.data=="string"]
                body=next((x for x in children if isinstance(x,Tree) and x.data=="body"),None)
                if terragrunt:
                    if name not in {"terraform","include","dependency","dependencies","inputs"}: result.append(self._gap(root,path,repository,commit,block.meta.line,"unsupported-terragrunt-construct","terragrunt","hcl")); continue
                    if name=="include": result += [self._item(root,path,repository,commit,block.meta.line,"terraform-composition",{"form":"terragrunt-include","language":"terragrunt-hcl","sourceSelectionId":self.selection["id"], **({"label":labels[0]} if labels else {})}), self._gap(root,path,repository,commit,block.meta.line,"unresolved-terragrunt-include","terragrunt","hcl")]
                    if name=="dependency" and labels: result += [self._item(root,path,repository,commit,block.meta.line,"terraform-composition",{"form":"terragrunt-dependency","language":"terragrunt-hcl","sourceSelectionId":self.selection["id"],"label":labels[0]}),self._gap(root,path,repository,commit,block.meta.line,"unresolved-terragrunt-dependency","terragrunt","hcl")]
                    if name=="inputs" and body:
                        for a in [x for x in body.children if isinstance(x,Tree) and x.data=="attribute"]:
                            key=_identifier(a.children[0]); result += [self._item(root,path,repository,commit,a.meta.line,"terraform-composition",{"form":"terragrunt-input-key","language":"terragrunt-hcl","sourceSelectionId":self.selection["id"],"key":key}),self._gap(root,path,repository,commit,a.meta.line,"unresolved-terragrunt-input","terragrunt","hcl")]
                    if name=="terraform" and body:
                        for a in [x for x in body.children if isinstance(x,Tree) and x.data=="attribute" and _identifier(x.children[0])=="source"]:
                            val=_literal_string(a.children[-1].children[0]) if isinstance(a.children[-1],Tree) and a.children[-1].children else None
                            if val: result.append(self._item(root,path,repository,commit,a.meta.line,"terraform-composition",{"form":"terragrunt-terraform-source","language":"terragrunt-hcl","sourceSelectionId":self.selection["id"],"source":val,"evidenceDisposition":"raw-text-safe" if _safe_line_span(text, a.meta.start_pos, a.meta.end_pos) else "inventory-only"}))
                            else: result.append(self._gap(root,path,repository,commit,a.meta.line,"dynamic-terragrunt-source","terragrunt","hcl"))
                    continue
                if name not in {"terraform","resource","data","module","provider","variable","output","locals"}: result.append(self._gap(root,path,repository,commit,block.meta.line,"unsupported-terraform-block")); continue
                if name=="locals" and body:
                    for a in [x for x in body.children if isinstance(x,Tree) and x.data=="attribute"]: result.append(self._item(root,path,repository,commit,a.meta.line,"terraform-declaration",{"declarationType":"local","key":_identifier(a.children[0]),"language":"terraform-hcl","loadRole":"override" if path.name.endswith("override.tf") else "ordinary","sourceSelectionId":self.selection["id"]}))
                else:
                    value={"declarationType":name,"language":"terraform-hcl","loadRole":"override" if path.name.endswith("override.tf") else "ordinary","sourceSelectionId":self.selection["id"],"evidenceDisposition":"raw-text-safe" if _safe_line_span(text, block.meta.start_pos, _block_header_end(block)) else "inventory-only"}
                    if labels: value["type" if name in {"resource","data"} else "name"]=labels[0]
                    if name in {"resource","data"} and len(labels)>1:value["name"]=labels[1]
                    result.append(self._item(root,path,repository,commit,block.meta.line,"terraform-declaration",value))
                if name=="module":
                    result.append(self._gap(root,path,repository,commit,block.meta.line,"unresolved-module-implementation"))
                    if body:
                        for a in [x for x in body.children if isinstance(x,Tree) and x.data=="attribute" and _identifier(x.children[0])=="source"]:
                            val=_literal_string(a.children[-1].children[0]) if isinstance(a.children[-1],Tree) and a.children[-1].children else None
                            if val: result.append(self._item(root,path,repository,commit,a.meta.line,"terraform-composition",{"form":"terraform-module-source","language":"terraform-hcl","sourceSelectionId":self.selection["id"],"module":labels[0] if labels else "","source":val,"evidenceDisposition":"raw-text-safe" if _safe_line_span(text, a.meta.start_pos, a.meta.end_pos) else "inventory-only"}))
                            else: result.append(self._gap(root,path,repository,commit,a.meta.line,"dynamic-module-source"))
            seen = set()
            for node in nodes:
                if node.data != "get_attr_expr_term": continue
                ref = _traversal(node)
                bits = ref.split(".") if ref else []
                accepted = ((bits[0] in {"var", "local", "module"} and len(bits) >= 2) or (bits[0] == "data" and len(bits) >= 3) or (len(bits) >= 2 and bits[0] not in {"var", "local", "module", "data"}))
                recorded = ".".join(bits[:3] if bits[0] == "data" else bits[:2])
                if accepted and (node.meta.line, recorded) not in seen:
                    seen.add((node.meta.line, recorded)); result.append(self._item(root,path,repository,commit,node.meta.line,"terraform-reference",{"reference":recorded,"language":"terraform-hcl","sourceSelectionId":self.selection["id"]}))
            if terragrunt:
                for attr in [x for x in nodes if x.data == "attribute" and _identifier(x.children[0]) == "inputs"]:
                    for element in [x for x in attr.iter_subtrees_topdown() if x.data == "object_elem"]:
                        key = next(( _identifier(x.children[0].children[0]) for x in element.children if isinstance(x,Tree) and x.data == "object_elem_key"), "")
                        if key:
                            result += [self._item(root,path,repository,commit,element.meta.line,"terraform-composition",{"form":"terragrunt-input-key","language":"terragrunt-hcl","sourceSelectionId":self.selection["id"],"key":key}),self._gap(root,path,repository,commit,element.meta.line,"unresolved-terragrunt-input","terragrunt","hcl")]
        return result, sorted(excluded,key=lambda x:(x["path"],x["reason"]))
