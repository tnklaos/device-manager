# Withdraw UI Flow

เอกสารนี้สรุป flow ปัจจุบันของหน้า withdraw ใน BCEL One ตามโค้ดที่ใช้อยู่ตอนนี้ใน `engine.py` และ `bcel.py`

## 1. Flow หลัก

ลำดับที่ backend รันตอนเตรียม withdraw device:

1. เช็ก current page ของ BCEL One ก่อน แล้ว login เฉพาะถ้าจำเป็น
2. เลือกบัตร UnionPay
3. ตรวจหน้า `Transfer Money`
4. เลือก source account
5. ตรวจหน้า `Receiver Account`
6. กรอกบัญชีปลายทาง
7. กดปุ่มไปหน้าถัดไป
8. ตรวจหน้า `Transfer Amount`
9. กรอกจำนวนเงิน
10. กดปุ่มไปหน้าถัดไป
11. จัดการหน้า `Security Answer` ถ้ามี
12. จัดการหน้า `Transfer Description` ถ้ามี
13. ตรวจหน้า `Transfer Confirmation`
14. คลิกปุ่ม `ໂອນເງິນ` / `Transfer`
15. แยก `success page` ออกจาก `error modal`
16. ถ้าสำเร็จ กด `ສໍາເລັດ` / `Done` แล้วเช็กว่ากลับ home
17. ถ้าล้มเหลว อ่านข้อความบน modal แล้ว throw fail
18. ถ้า flow fail ระหว่างทาง ให้พยายาม recover กลับ home

อ้างอิงจาก `engine.Engine._prepare_withdraw_device(...)`

## 2. การเริ่มต้นตอน receive request

ตอนนี้ `connect(...)` ไม่ได้ restart app ทุกครั้งแล้ว แต่จะเช็ก state ปัจจุบันก่อน:

1. ถ้า `fresh=True`
   - restart แอป
2. ถ้า current package เป็น BCEL อยู่แล้ว
   - ปิด popup ที่บังหน้า ถ้ามี
   - ถ้าเป็น login screen ให้ login
   - ถ้าอยู่ home อยู่แล้ว ให้ reuse ต่อทันที
   - ถ้าอยู่กลาง flow/page อื่น ให้พยายาม `go_home(...)`
3. ถ้ายังไม่ได้อยู่ใน BCEL
   - ค่อย `app_start(...)`

สรุป:
- ถ้าอยู่ home page แล้ว จะไม่ login ใหม่
- ถ้า session ยังอยู่ จะ reuse app state เดิม
- login จะเกิดเฉพาะตอนเจอ login screen จริงเท่านั้น

## 3. หน้าที่ระบบรู้จัก

### 3.1 Transfer Money

ชื่อหน้าที่ใช้เช็ก:
- `ການໂອນເງິນ`
- `Transfer Money`

สิ่งที่ระบบทำ:
- เช็กว่าอยู่หน้าโอนเงิน
- หา source account จากเลขบัญชีที่ save ไว้
- คลิกแถวของบัญชีนั้น

### 3.2 Receiver Account

ชื่อหน้าที่ใช้เช็ก:
- `ບັນຊີປາຍທາງ`
- `Receiver Account`
- `Receivers Account`

สิ่งที่ระบบทำ:
- เช็กว่ามาถึงหน้าบัญชีปลายทาง
- กรอก `toAccount` จาก request
- กดปุ่มไปหน้าถัดไป

### 3.3 Transfer Amount

ชื่อหน้าที่ใช้เช็ก:
- section `ຈາກບັນຊີ` / `From Account`
- section `ຫາບັນຊີ` / `To Account`
- ชื่อผู้รับต้องตรงกับ `toName`

สิ่งที่ระบบทำ:
- ตรวจว่า source account ตรง
- ตรวจว่า destination account ตรง
- ตรวจว่าชื่อผู้รับตรง
- กรอกจำนวนเงิน
- กดปุ่มไปหน้าถัดไป

### 3.4 Security Answer

ชื่อหน้าที่ใช้เช็ก:
- title `ຢັ້ງຢືນຕົວຕົນເພີ່ມເຕີມ`
- หรือมี `pageq1/pageq2/pageq3`
- หรือมี `ans1/ans2/ans3`

ข้อความที่ใช้บอกข้อ:
- `ຄໍາຖາມທີ 1/2/3`
- `ຄຳຖາມທີ 1/2/3`
- `Question 1/2/3`

ช่อง input ที่ใช้:
- `ans1`
- `ans2`
- `ans3`

สิ่งที่ระบบทำ:
1. ถ้าอยู่หน้า security answer จะหา current question index ก่อน
2. ลองพิมพ์ answer ใส่ active cursor ก่อน
3. กดปุ่มไปหน้าถัดไป
4. เช็กว่าหน้าเปลี่ยนหรือไม่
5. ถ้าไม่เปลี่ยน ค่อย fallback ไปคลิก input จริง (`ans1/ans2/ans3`) แล้วพิมพ์ใหม่
6. ทำซ้ำจนหมดทุกข้อ

จำนวนหน้าที่รองรับ:
- 0 หน้า: ข้าม
- 1 หน้า: answer 1 แล้วจบ
- 2 หน้า: answer 1 -> answer 2
- 3 หน้า: answer 1 -> answer 2 -> answer 3

### 3.5 Transfer Description

ชื่อหน้าที่ใช้เช็ก:
- `ຄໍາອະທິບາຍການໂອນ`
- `ຄຳອະທິບາຍການໂອນ`
- `Transfer Description`

placeholder ที่ใช้:
- `ປ້ອນຄໍາອະທິບາຍ`
- `ປ້ອນຄຳອະທິບາຍ`

input ที่ควร match:
- `rid=desc`
- `class=android.widget.EditText`

สิ่งที่ระบบทำ:
1. ถ้าตรวจเจอหน้า description จะลองพิมพ์ใส่ active input ก่อน
2. ถ้า active input path ไม่ผ่าน จะ fallback ไปหา input ใต้ title/placeholder
3. ให้ `rid=desc` เป็นตัวเลือกอันดับแรก
4. กรอกค่า `remark`
5. กดปุ่มไปหน้าถัดไป

### 3.6 Transfer Confirmation

สิ่งที่ระบบเช็ก:
- section `ຈາກບັນຊີ` / `From Account`
- section `ຫາບັນຊີ` / `To Account`
- ชื่อผู้รับต้องตรงกับ `toName`

แนวทาง:
- ใช้เงื่อนไขตรวจแบบเดียวกับหน้า `Transfer Amount`
- ถ้าตรวจผ่าน จะ log `confirm page verified`
- หลังจากนั้นระบบจะคลิกปุ่ม `ໂອນເງິນ` / `Transfer`
- หลังคลิกแล้วระบบจะเช็กก่อนว่าเป็น `success page` หรือ `error modal`

### 3.7 Transfer Success

ตัวอย่างจุดสังเกต:
- title ประเภท `ໂອນເງິນສໍາເລັດ`
- ปุ่ม `ສໍາເລັດ` / `Done`
- ข้อมูลใบเสร็จ เช่น `ເລກ Ticket`

สิ่งที่ระบบทำ:
1. ถ้าตรวจเจอหน้า success จะ log `transfer success`
2. กดปุ่ม `ສໍາເລັດ` / `Done`
3. เช็กว่ากลับหน้า home หรือยัง
4. ถ้ากลับ home แล้ว log `current page: home`

หมายเหตุ:
- หน้า success จะไม่ถูกตีความเป็น fail modal

### 3.8 Transfer Error Modal

ตัวอย่างปุ่มบน modal:
- `ຕົກລົງ`
- `OK`
- `Close`
- `ປິດ`

สิ่งที่ระบบทำ:
1. อ่านข้อความกลาง modal
2. log `transfer failed: ...`
3. raise error กลับไปที่ engine

หมายเหตุ:
- `remark` อ่านจาก request field ตามลำดับนี้:
  - `remark`
  - `note`
  - `description`

## 4. ปุ่มไปหน้าถัดไป

ปุ่มที่ helper ใช้กดได้ตอนนี้:
- `ຕໍ່ໄປ`
- `Next`
- `ເພີ່ມບັນຊີ`
- `Add Account`

เหตุผล:
- บางหน้าใช้ `Next`
- บางหน้าสุดท้ายของ security flow ใช้ `Add Account`

## 5. แนวคิด fallback

หลักการที่ใช้ตอนนี้:

1. พยายามใช้ `active cursor` ก่อน ในหน้าที่ UI จริงมัก focus ให้แล้ว
2. ถ้า path นี้ไม่ทำให้หน้าเปลี่ยน ค่อย fallback ไปหา input จาก hierarchy
3. ถ้าหา input จาก hierarchy:
   - ใช้ `resource-id` ก่อน
   - ถ้าไม่มี ใช้ label / hint / class / ตำแหน่งใต้ข้อความ

## 6. Fail Recovery

ถ้า flow fail ระหว่างทาง `engine` จะเรียก `recover_to_bcel_home(...)`

สิ่งที่ recovery ทำ:
1. พยายามปิด popup ก่อน
   - `ຕົກລົງ`
   - `OK`
   - `Close`
   - `ປິດ`
2. ถ้ายังอยู่ใน BCEL จะใช้ `go_home(...)`
3. ถ้ากลับ home สำเร็จ จะ log `back to home`

สรุป:
- fail แล้วจะไม่ค้างอยู่หน้ากลาง flow ถ้ากลับ home ได้
- ทำให้ request รอบถัดไปเริ่มจาก state ที่คาดเดาได้ง่ายขึ้น

## 7. Log แบบใหม่

ตอนนี้ log ถูกลดให้สั้นลงเพื่อให้อ่าน flow ง่ายขึ้น และมี log สำหรับ success/fail หลังโอนชัดขึ้น

ตัวอย่าง:
- `reuse current BCEL home`
- `resume BCEL from .PageActivity`
- `verified transfer money page`
- `verified receiver account page`
- `entered receiver account from request: ...1234`
- `verified transfer amount page: ...`
- `entered transfer amount: 50000`
- `security question 1`
- `answer 1 entered`
- `security answers done`
- `transfer description`
- `description entered`
- `clicking receiver next: ຕໍ່ໄປ`
- `confirm page verified`
- `click transfer: ໂອນເງິນ`
- `transfer success`
- `current page: home`
- `transfer failed: ...`
- `back to home`

## 8. จุดที่ยังควรจับตา

1. หน้า WebView บางหน้ามี cursor active แต่ hierarchy ไม่สวย
2. บาง input เป็น custom control แม้หน้าตาเหมือน `EditText`
3. ADB keyboard `clear=True` เคยพังกับบางหน้า
4. ดังนั้น flow ตอนนี้พยายามใช้ keyevent clear + `send_keys(..., clear=False)` มากกว่า
5. หน้า success กับ modal fail ต้องแยกด้วย anchor หลายตัวร่วมกัน ไม่ควรดูแค่มีปุ่ม `Close`

## 9. ไฟล์ที่เกี่ยวข้อง

- `engine.py`
  - `Engine._prepare_withdraw_device(...)`
- `bcel.py`
  - `connect(...)`
  - `verify_transfer_money_page(...)`
  - `select_source_account_on_transfer_page(...)`
  - `verify_receiver_account_page(...)`
  - `input_receiver_account(...)`
  - `verify_transfer_amount_page(...)`
  - `input_transfer_amount(...)`
  - `input_security_answer(...)`
  - `input_transfer_description(...)`
  - `verify_transfer_confirmation_page(...)`
  - `click_transfer_confirm(...)`
  - `check_transfer_result_modal(...)`
  - `recover_to_bcel_home(...)`
  - `click_receiver_next(...)`
