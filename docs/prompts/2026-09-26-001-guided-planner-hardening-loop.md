---
title: Rehberli Sorgu Planlayıcıyı Adım Adım Sağlamlaştırma Turu - Prompt
type: prompt
date: 2026-09-26
execution: code
---

# Rehberli Sorgu Planlayıcıyı Adım Adım Sağlamlaştırma Turu

**Target repo:** laya-jev-GraphRAG. Kod `graphrag_neo4j_laya/` altında; tüm yollar repo köküne göredir.

Bu doküman, 2026-09-25/26'da yapılan yedi turu **başka bir oturumda tekrar edebilmek** için
yazıldı. İçinde üç şey var: turun yöntemi (§2), o yöntemle şimdiye dek ne yapıldığı ve neyin
çürütüldüğü (§4 — aynı duvara ikinci kez toslamamak için), ve doğrudan yapıştırılabilir
prompt metni (§6).

---

## 1. Ne yapıyoruz

Laya 322M bir sınıflandırıcı: metin **üretemez**, yalnız verilen adaylar arasından **seçer**
(Pangu'nun "üretme, ayırt et" ilkesi). Planlayıcı bu yüzden sorguyu adım adım kuruyor — işlem,
başlangıç, hop'lar, filtreler, şekil, anlamsal filtre — ve her adımda yasal hamleleri kod
üretip modele yalnız seçtiriyor. Cypher'ı yalnız `render_kuzu.py` yazar; tanımlayıcılar beyaz
listeden gelir, bütün değerler parametredir, kullanıcı metni sorguya hiç girmez (R6).

Turun tek cümlelik özeti:

> **Kararın ifadeden okunabilen kısmını koda al, kalanını modele bırak — ve her seferinde
> gerçek Laya ile ölç.**

Yedi turun sonucu: sonuç eşleşmesi %8,3 → %91,7, tam plan %0 → %75,0, soru başına `Choice`
3,9 → 2,7. Üç bayrak hedefinin ikisi tuttu. Ayrıntılar `graphrag_neo4j_laya/AGGREGATION_METHODS.md`
§7'de; o bölüm turun **kayıt defteri**, her tur oraya yazılır.

---

## 2. Turun yöntemi

Her tur tek bir adımı hedefler ve şu sırayla gider. Sıra önemli: 3. maddeyi atlayıp ölçüme
koşmak, ölçümü yakan en sık hata.

1. **Hatayı oku.** `benchmarks/results/aggregate_planner_eval.json` içindeki `records`, soru
   başına `steps`, `gold_hops`/`plan_hops`, `gold_shape`/`plan_shape`,
   `gold_semantic`/`plan_semantic` taşır. Başarısız adımları listele ve **desenini** bul: aynı
   hata kaç soruda, hangi ortak ifadeyle?
2. **Karar ifadeden okunabiliyor mu?** Okunabiliyorsa kod karar verir. Okunamıyorsa seçenek
   kümesini daralt (tanım gereği yanlış olanları çıkar) ve kararı modele bırak. Tek seçenek
   kalırsa sorma — anlamsız bir olasılık plan güvenine karışır.
3. **Kuralı ölçmeden önce etiketli sette doğrula.** Küçük bir betikle 34 sorunun hepsinde kuralı
   çalıştır, altın planla karşılaştır, **yanlış pozitif sayısını sıfırda gör**. Buradan
   geçmeyen kural ölçüme gitmez. (Bu adım şimdiye dek her turda tuttu ve ölçümü öngördü.)
4. **Kodu yaz.** Yorum, kuralın *ölçülmüş gerekçesini* taşısın ("Measured: offered `stop` here,
   the model took it in all four two-hop questions"), tarifini değil.
5. **Testleri düzelt.** Bir adım artık `Choice` tüketmiyorsa `ScriptedModel` kuyrukları kayar ve
   10–14 test birden kırılır; bu **beklenen** bir sonuçtur. Kuyrukları güncelle, yeni davranış
   için test ekle. **İddiaları zayıflatarak testi geçirme.** Kırılan bir kabul senaryosu (AE1–AE3)
   kuralın yanlış olduğunun işaretidir — nitekim geri dönüş kuralı böyle yakalandı.
6. **Ölç.** Tam düzenek, gerçek Laya, 34 soru (§3).
7. **Yaz.** `AGGREGATION_METHODS.md` §7'ye: tabloyu güncelle (parantez içinde bir önceki ölçüm),
   bulguyu ekle. **Çürüyen hipotezi ve geri alınan denemeyi de yaz** — bunlar bir sonraki
   oturumun en değerli bilgisi.
8. **Commit & push.** Commit mesajı ölçümü taşısın (önce/sonra), gerilemeyi de yazsın.

### Bir turda tek değişiklik

İki kuralı aynı ölçüme sokma. Bir kere yapıldı (anahtar ifadesi + PageRank kısıtı); metrik
%83,3 → %75,0 geriledi ve hangisinin yaptığı ancak soru bazlı farkla ayrıldı. Adımları farklı
olan iki değişiklik birlikte ölçülebilir, çünkü `steps` onları ayırır.

### Kapasite kaybına dikkat

Bir kural ölçümü iyileştirip gramerin bir soru biçimini imkânsız kılabilir. Geri dönüş kuralı
(`BORN_IN:out` → `BORN_IN:in`) ölçümü çok iyileştirdi ama AE3'ü ("Einstein'ın doğduğu yerde doğan
**başka** kim var?") kırdı. Doğru çözüm kuralı atmak değil, istisnayı **ifadeden okumak** oldu:
"başka / other / else / diğer". Bir kabul testi kırıldığında önce istisnayı ara.

---

## 3. Düzenek

```bash
cd graphrag_neo4j_laya
python -m venv .venv                     # .venv gitignore'da
.venv/Scripts/pip install kuzu torch transformers sentence-transformers huggingface_hub \
    numpy networkx pydantic pydantic-settings python-dotenv pytest
.venv/Scripts/python.exe -m pytest tests/ -q        # ~20 sn, hepsi geçmeli
.venv/Scripts/python.exe -m graphrag.benchmarks.aggregate_planner_eval   # ~2-4 dk
```

- `requirements.txt`'in tamamı gerekmiyor; yukarıdaki asgari küme yetiyor. İçindeki
  `ragatouille` ve `bitsandbytes` bu iş için **gerekmiyor**, kurulumu uzatıp hata veriyorlar.
  (Ölçüm yapılan kurulumda: kuzu 0.11.3, torch 2.14.0+cpu, transformers 5.17.0.)
- İlk çalıştırmada Laya checkpoint'i HF'den iner (~1 dk, `multilingual`, CPU).
- Ölçüm 34 soruluk etiketli seti (`examples/data/aggregate_eval.json`) çalıştırır: 24'ü
  agregasyon, 14'ü Türkçe. Seed'ler setten gelir, seed hatası sayılara karışmaz.
- Sonuç `benchmarks/results/aggregate_planner_eval.json`'a yazılır ve **git'te izlenir** —
  her turda commit'e girer, bir sonraki turun karşılaştırma tabanıdır.
- Ölçümü arka planda çalıştır, çıktıyı scratchpad'e yönlendir; Windows'ta doğrudan `$SCRATCH`
  yok, mutlak yol kullan.
- Gecikme ölçümler arasında 3–4 kat oynuyor (makine yükü). Mutlak değere değil, sıralamaya bak.

**Bayrak hedefleri:** sonuç eşleşmesi ≥ %80, hop yönü ≥ %90, yanlış yönlendirme %0.
İlk ikisi tuttu; üçüncüsü router'ın işi, planlayıcının değil.

---

## 4. Şimdiye kadarki turlar

Kronolojik; her satır bir commit. Ölçümler gerçek Laya, aynı 34 soru.

| # | Tur | Ölçülen etki |
| --- | --- | --- |
| 0 | Başlangıç adımını koda al (seed adı soruda geçiyor mu) | başlangıç %41,7 → %100, ama hop'lar **geriledi** |
| 1 | Anchor'lı planda hop0'da `stop` sunma | hop tipi %8,3 → %37,5, yön → %50,0, durma → %54,2 |
| 2 | İşlem adımı: dört kural (üstünlük yoksa `rank` yok; "kaç" sorusu liste değil; dağıtıcı işaret → `group`; eşik → `group`) | işlem %70,8 → **%100**, anahtar → %87,5, metrik → %75,0 |
| 3 | İlk hop: ilişkiden söz etmeyen soru durur; tek tip anılıyorsa o tip | hop tipi %37,5 → %75,0, yön → %75,0, sonuç → %25,0 |
| 4 | Şemaya Türkçe ilişki sözcükleri (`words`) | hop tipi → %79,2, sonuç → %33,3, tr sonuç %20 → %40 |
| 5 | Yol uzunluğu: "two steps away" (kesin) ve ilişkiye iki atıf (alt sınır) | durma %79,2 → %91,7, sonuç → %37,5 |
| 6 | Geri dönüşü sunma (aynı tip ters yön), "başka" istisnasıyla | hop tipi → **%95,8**, yön → **%91,7**, sonuç → %41,7 |
| 7 | Şekil: anahtar alanı ifadeden + tek `Choice`; metrik ifadeden; `HAVING` eşikten | anahtar/metrik/`HAVING` → **%100**, sonuç → %66,7 |
| 8 | Anlamsal filtre: yalnız sorunun andığı tür, hop'un ima ettiği düşülerek | anlamsal %70,8 → **%95,8**, sonuç → **%91,7**, tam plan → %75,0 |
| 9 | Filtre: iki+ hop'ta başlangıç varlığı kodda hariç tutulur | filtre %83,3 → %87,5, tam plan → %79,2 |
| 10 | Filtre: sayısal filtre yalnız soru PageRank/topluluk anıyorsa (ve `rank` değilse) | filtre → **%95,8**, tam plan → **%87,5**, tr tam plan **%100** |
| 11 | Anılan ilişki tipi, onu sunabilen ilk hop'ta harcanır (yalnız hop0'da değil) | hop tipi → **%100**, durma → **%100**, filtre → **%100**, sonuç → **%95,8** |
| 12 | Soru cevabını adlandırmışsa ("everything", "varlık") andığı tür cevaba ait değil | anlamsal → **%100**, **sonuç %100**, tam plan **%95,8** |
| 13 | Bütün graph'tan atılan ilk hop `out` (soru "incoming/gelen" demedikçe) | yön → **%100**, **tam plan %100** — her adım 24 / 24 |

### Çürütülen varsayımlar — tekrar denemeyin

- **"Başlangıç yanlış olunca hop'lar da yanlış."** Yanlış. Başlangıç %100 olunca hop'lar
  geriledi; gerçek sebep hop0'da sunulan `stop`'tu.
- **`ask_batch` tek forward pass değil (KTD6'nın dayandığı varsayım).** Gecikme seçenek sayısıyla
  artıyor. Hop adımını seçenek başına `Noul`'a çevirme denemesi her yerde geriledi (hop tipi
  %37,5 → %12,5) ve plan süresi 0,95 → 5,26 sn'ye çıktı. **Geri alındı.**
- **`ask_batch` ile çoklu seçim ayırt etmiyor.** Gruplama anahtarlarında doğru aday da yanlışı da
  0,75–0,96 arasında; argmax 7'de 3. Planlayıcı artık `ask_batch`'i hiç çağırmıyor.
- **Metrik `Choice`'ı bir tercihe saplanıyor.** PageRank seçenekleri açıkken onları, kapalıyken
  altı grubun altısına da `count_distinct` seçti.
- **Geri dönüş kuralını kayıtsız şartsız koymak.** AE3'ü kırar; istisna ifadeden okunmalı.
- **Sorudaki sayıyı düğümün bir alanına bağlatmak.** `Noul` "evet" deyip model rastgele alan
  seçiyordu (`v0.communityId < 2`, `v0.pagerank < 1`). Düğümün taşıdığı sayı iki tanedir; soru
  onları anmıyorsa sayı şekil adımının (`limit` ya da eşik).

### Doğrulanan

- **KTD5:** plan güveni en zayıf adımın olasılığıdır, çarpım değil. Ayırma gücü min %85,2'ye
  karşı çarpım %84,3; doğru plan çıkmaya başlayınca fark ölçülebilir hale geldi.
- Kod kararına alınan her adım, arkasındaki adımları da düzeltti (işlem düzelince anahtar ve
  metrik; hop düzelince filtre). `t04`'te ikinci hop düzelince model üçüncü hop'u kendiliğinden
  bıraktı — planlanan "alt sınırı kesin sayı yap" turu gereksiz kaldı. Bir adımın hatası
  sonrakilerin hatası gibi görünebiliyor: ölçümü hep en erken bozulan adımdan onarın.
- Adımlar koda geçtikçe soru başına `Choice` 3,9'dan 2,7'ye, plan süresi düştü.

---

## 5. Sıradaki turlar (ölçülmedi)

1. **Etiketli set doydu — turlar burada bitti.** Her adım 24 / 24, tam plan ve sonuç %100.
   Set artık planlayıcıyı ölçmüyor: yeni kuralı doğrulayacak yanlış plan kalmadı, güven ayırma
   gücü bile hesaplanamıyor. Devam etmek için **yeni sorular** gerekiyor — üç ve daha fazla hop,
   birden çok anahtar, birden çok tür, sayısal alan filtresi, `in` yönünde bütün-graph sorusu.
   Yeni soru eklerken altın planları elle yazın ve önce mevcut planlayıcıyla ölçün.
2. **Geri çeviri kontrolü** — artık en zayıf halka: doğru planların yarısını reddediyor (%50),
   yanlışların üçte birini geçiriyor. Her adım doğruyken kontrolün planı `None`'a çevirmesi net
   kayıp.
3. **Router** — tutmayan tek bayrak hedefi burada: agregasyon sorularının yalnız %37,5'i bu
   rotaya giriyor, yanlış yönlendirme %10. Planlayıcı artık doğru plan kuruyor; darboğaz
   yönlendirme.
4. **Jev backend'ini aynı düzenekle ölçmek** (`DECISION_MODEL_BACKEND=jev`).

---

## 6. Yapıştırılabilir prompt

> `graphrag_neo4j_laya/AGGREGATION_METHODS.md` §7'deki rehberli sorgu planlayıcısını bir tur daha
> sağlamlaştır. Yöntem `docs/prompts/2026-09-26-001-guided-planner-hardening-loop.md` §2'de yazılı,
> aynen uygula:
>
> 1. `benchmarks/results/aggregate_planner_eval.json` içindeki soru bazlı `steps` alanlarından
>    **<ADIM ADI>** adımının hatalarını çıkar ve ortak deseni bul.
> 2. Kararın ifadeden okunabilen kısmını koda al, kalanı için seçenek kümesini daralt; tek
>    seçenek kalırsa sorma.
> 3. Kuralı **ölçmeden önce** 34 sorunun hepsinde çalıştırıp altın planla karşılaştır; yanlış
>    pozitif sıfır değilse kuralı düzelt.
> 4. Kodu yaz; yorumlar ölçülmüş gerekçeyi taşısın. Kırılan `ScriptedModel` kuyruklarını
>    güncelle, yeni davranış için test ekle, iddiaları zayıflatma. Bir kabul senaryosu (AE1–AE3)
>    kırılıyorsa kuralın istisnasını ifadeden okumaya çalış.
> 5. `.venv/Scripts/python.exe -m graphrag.benchmarks.aggregate_planner_eval` ile ölç.
> 6. Sonucu §7'ye yaz: tabloyu güncelle (parantezde bir önceki ölçüm), bulguyu ekle, **çürüyen
>    hipotezi ve geri aldığın denemeyi de yaz**.
> 7. Commit & push; mesaj önce/sonra sayılarını ve varsa gerilemeyi taşısın.
>
> Bir turda tek değişiklik ölç. §4'teki çürütülmüş varsayımları tekrar deneme.

`<ADIM ADI>` yerine: `filters` (sıradaki), `semantic`, `hop_types`, `metrics`, ya da router için
`route_accuracy`.

---

## 7. Değişmezler

Bunlar tur sırasında **pazarlık konusu değil**:

- Cypher'ı yalnız `render_kuzu.py` yazar. Tanımlayıcılar beyaz listeden, bütün değerler
  parametre, kullanıcı metni sorguya girmez.
- Geçersiz hamle üretilemez: hop seçenekleri o anki frontier'dan gerçekten çıkan ilişki tipi ×
  yön çiftleridir.
- Kùzu 0.11.3 `count(DISTINCT ...)`'tan sonraki agregaları sessizce sıfırlar; renderer DISTINCT
  agregaları en sona koyar.
- Kesilme `limit + 1` ile saptanır (KTD8); boş sonuç geçerli bir cevaptır (KTD9).
- `KuzuClient.close()` her iki handle'ı da kapatır — Windows'ta açık dosya dizini sildirmiyor ve
  bir ölçüm bu yüzden çöpe gitmişti.
- Ölçüm sonucu JSON'u git'te izlenir; her turda commit'e girer.
