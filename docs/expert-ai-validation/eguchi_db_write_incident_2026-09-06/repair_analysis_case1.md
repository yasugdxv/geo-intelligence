# Case1 OFF/ON — Repair分析（Trump/Xi/Congress/BISの追跡）

作成日: 2026-09-06
手法: コード修正・LLM再実行なし。保存済みの`docs/expert-ai-validation/
eguchi_tier1_off_on_case1_trump_xi/{off,on}_result.json`の`execution_metadata`
（`initial_failed_checks_detail`/`stage_metrics`）と、`geo_expert_quality_guard.py`の
該当チェック関数のソースコードのみから追跡した。

## 確定した事実

### ①Stage1 raw → 両RunともActor 0件

`geo_expert_quality_guard._key_stakeholder_v12_check`（247-266行）のソースコードを確認:

```python
if not (response.get("key_actor_profiles") or []):
    return _check(name, False,
                   f"determination={ksa.get('determination')}の場合はkey_actor_profilesが1件以上必要です")
```

この関数は`key_actor_profiles`が空（`[]`または未設定）の場合にのみこの文言で失敗する。
OFF・ON両方の`initial_failed_checks_detail`に`key_stakeholder_v12_fields_present`が
「determination=distributedの場合はkey_actor_profilesが1件以上必要です」という**全く同じ
文言**で記録されている。したがって**Stage1 raw出力（Repair前）の時点で、OFF・ON両方とも
`key_actor_profiles`が0件だった**ことが確定する。Trump・Xi・Congress・BISのいずれも、
この時点ではまだ存在しない。

### ②Guard前（pre-repair guard_result） → 同上

`guard_result`は`_merge_staged_response(stage1_data, stage2_data, ...)`直後の`response_json`
に対して実行される（Stage1/Stage2ともこの時点では未修復）。①と同じ内容のため、Actor状況も
同じ（0件）。

### ③Repair input → stage1_data（Actor 0件）

`MODE_V12_CHECK_STAGE`（`geo_expert_quality_guard.py:1818-1823`）:
```python
"key_stakeholder_v12_fields_present": "stage1",
```
`geo_expert_engine.py`の該当箇所で`target_stage=="stage1"`のため
`repair_base_data = stage1_data`（Stage1 raw、Actor 0件）がRepairへ渡される。両Runの
`stage_metrics`に`"stage": "repair:stage1"`が記録されており、これを直接裏付ける。

Repairプロンプトは以下の構造（`geo_expert_engine.py`のrepair_prompt構築部分）:
```text
{user_prompt}

【修復依頼】
以下は元の出力（該当スコープのみ）です。下記のチェックに失敗しています。
既存の内容は維持したまま不足箇所のみ補ってください。ゼロから作り直さないこと。
Evidence IDを創作しないこと。
【失敗したチェック】
{failed_detail}  ← guard_result内の"全ての"failed checksの名前+detail（v12以外も含む）

【元の出力】
{stage1_data}
```

`failed_detail`にはv12チェック以外の失敗（temporal_hedge_language_not_present等、
advisory・非repair対象）も**全て**含まれる。つまりRepairは「key_actor_profilesを埋める」
以外の複数の指摘にも同時に対応するよう求められる、**max_attempts=1の一発勝負**。

OFF側のfailed_detail（3件）:
- `key_stakeholder_v12_fields_present`（Actor0件）
- `temporal_hedge_language_not_present`（as_of表現の時点混乱注意）
- `key_point_controllers_covered`（Primary Key Pointの制御主体「米連邦議会」がKey Actorに無い）

ON側のfailed_detail（5件、OFFに無い2件を含む）:
- 上記3件と同じもの
- **`fact_claims_have_source_ref`**（Fact/Official Claim/Observationに根拠参照が無い主張がある）
- **`supporting_knowledge_refs_grounded`**（`supporting_knowledge_refs`のtype=institution_
  intelligence、ref=`63fa2294-ceca-488f-8e76-d4dae736a8ce`（BISのentity_id）が、その時点の
  `key_actor_profiles[].entity_id`に実在しない——つまりStage1(ON)は、BISのKey Actor
  Profileを作る前に、どこか別の場所でBISのInstitution Intelligenceを`supporting_knowledge_refs`
  として引用しようとしていた）

### ④Repair後（最終） → OFF=3件、ON=1件

| | OFF（Repair後） | ON（Repair後） |
|---|---|---|
| Trump | ✗ | ✗ |
| Xi | ✗ | ✗ |
| 米連邦議会/米議会 | ✓（perceived_path/event_role無し、旧Schemaのため） | ✓（**perceived_path/event_role両方生成**） |
| BIS | ✓ | ✗ |
| 中国政府・党指導部 | ✓ | ✗ |
| remaining_failed_checks | `actor_intelligence_provenance_present`（1件） | なし（0件） |

## 結論

1. **「Repairが既存のgroundedなActorを削除した」という当初の懸念は成立しない**。
   削除対象となるgroundedなActor自体が、両RunともRepair前には存在しなかった。
2. 実際に観測された差は、**「Repairが同じ1回の呼び出しで解決すべき失敗チェック数」の差**
   （OFF=3件、ON=5件）であり、ON側はより多くの問題を同時に解決する必要があった。
   結果としてON側のRepairは「BISへの不正参照を除去して`supporting_knowledge_refs_grounded`
   を通す」という最小対応に留まり、OFF側のような複数Actorのフル新規生成までは至らず、
   Congress 1件のみを生成した。ただしその1件は`perceived_path`/`event_role`を伴う
   質の高い生成物であり、「手抜き」ではなく「対象を絞った」形跡が見られる。
3. ON側で増えた2件の失敗チェック（`fact_claims_have_source_ref`/`supporting_knowledge_refs_
   grounded`）自体はTier1が新設したチェックではなく既存チェックであり、Stage1(ON)の生成が
   たまたまこれらに抵触しただけである可能性が高い。Tier1が追加したプロンプト分量
   （Candidate Generation Operators等）がStage1の生成の一貫性をわずかに低下させ、结果として
   こうした抵触を誘発した可能性はあるが、**n=1のため因果関係を断定できない**。
4. **Repairの決定的な設計不具合は見つからなかった**。ユーザー方針（「明確な不具合が無い場合は
   同条件ONの再実行より、複数の人物Actorが残るケースへの移行を優先」）に従い、次は
   Trump/Xiのような人物間相互作用が回答に不可欠なケースへ進むことを推奨する。

## 副次的な観察（今回のスコープ外、記録のみ）

Repairプロンプトが「ゼロから作り直さないこと」と明記しているにもかかわらず、実際には
`key_actor_profiles`が空の状態からのRepairは実質的に「ゼロから作る」役割を担わされている
（Stage1が最初から空で返した場合、Repairの「補うだけ」という設計意図と矛盟する）。これは
Tier1固有の問題ではなく、v12 Repair機構自体の既存の設計特性であり、今回のスコープでは
修正しない。
