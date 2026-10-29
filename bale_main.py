    elif text == "📦 بازار کالا":
        await message.answer("⏳ در حال دریافت آمار معاملات کالا...")
        try:
            # 👇 فعلاً برای تست، از یک تاریخ مطمئن استفاده می‌کنیم 👇
            test_date = "1404-03-18" 
            
            url = f"https://Api.BrsApi.ir/IME/Physical.php?key={BRS_API_KEY.strip()}&date_start={test_date}&date_end={test_date}"
            headers = {'User-Agent': 'Mozilla/5.0'}
            
            res = await asyncio.to_thread(requests.get, url, headers=headers, timeout=10)
            
            if res.status_code == 200:
                data = res.json()
                if not data:
                    await message.answer("⚠️ برای این تاریخ معامله‌ای در بورس کالا ثبت نشده است.")
                    return

                # پیدا کردن آخرین معامله برای کالاهای مهم
                target_commodities = {
                    "مس کاتد": "پایه سیم و کابل",
                    "آهن اسفنجی": "جایگزین آهن‌قراضه در کوره",
                    "گندله سنگ آهن": "مواد اولیه کوره بلند",
                    "وکیوم باتوم": "پایه قیر و آسفالت",
                    "شمش آلومینیوم": "فلز سبک صنایع",
                    "کنسانتره سنگ آهن": "پودر غلیظ‌شده سنگ آهن"
                }
                
                found_items = {}
                
                # جستجو در لیست معاملات
                for item in data:
                    name = item.get('l30', '')
                    for key in target_commodities.keys():
                        if key in name:
                            price_toman = item.get('pc', 0) / 10
                            volume = item.get('volume_contract', 0)
                            
                            if key not in found_items or item.get('date_trade') > found_items[key]['date']:
                                found_items[key] = {
                                    "price": price_toman,
                                    "volume": volume,
                                    "date": item.get('date_trade')
                                }

                # ساخت متن پیام
                msg = f"📦 {md.bold('آخرین معاملات بورس کالا')}\n⏰ {md.code(time_str)}\n\n"
                
                # دسته ۱: فلزات اساسی
                msg += f"🥇 {md.bold('فلزات اساسی:')}\n"
                if "مس کاتد" in found_items:
                    i = found_items["مس کاتد"]
                    msg += f"🔹 {md.bold('مس کاتد')} ({target_commodities['مس کاتد']})\n   قیمت: {md.code(f'{i['price']:,.0f}')} تومان | حجم: {md.code(f'{i['volume']:,}')} تن\n"
                if "شمش آلومینیوم" in found_items:
                    i = found_items["شمش آلومینیوم"]
                    msg += f"🔹 {md.bold('شمش آلومینیوم')} ({target_commodities['شمش آلومینیوم']})\n   قیمت: {md.code(f'{i['price']:,.0f}')} تومان | حجم: {md.code(f'{i['volume']:,}')} تن\n"
                
                # دسته ۲: فولاد و مواد معدنی
                msg += f"\n🏗️ {md.bold('فولاد و مواد معدنی:')}\n"
                for key in ["آهن اسفنجی", "گندله سنگ آهن", "کنسانتره سنگ آهن"]:
                    if key in found_items:
                        i = found_items[key]
                        msg += f"🔹 {md.bold(key)} ({target_commodities[key]})\n   قیمت: {md.code(f'{i['price']:,.0f}')} تومان | حجم: {md.code(f'{i['volume']:,}')} تن\n"
                
                # دسته ۳: انرژی و فرآورده‌ها
                msg += f"\n🛢️ {md.bold('انرژی و فرآورده‌ها:')}\n"
                if "وکیوم باتوم" in found_items:
                    i = found_items["وکیوم باتوم"]
                    msg += f"🔹 {md.bold('وکیوم باتوم')} ({target_commodities['وکیوم باتوم']})\n   قیمت: {md.code(f'{i['price']:,.0f}')} تومان | حجم: {md.code(f'{i['volume']:,}')} تن\n"

                msg += "\n_(قیمت‌ها بر اساس آخرین معامله ثبت‌شده در این تاریخ است)_"
                await message.answer(msg)
            else:
                # اگر خطا داد، خود متن خطای سرور رو چاپ می‌کنیم تا بفهمیم چرا 400 داده
                await message.answer(f"❌ خطای سرور (کد {res.status_code}).\nمتن خطا: {res.text[:200]}")
        except Exception as e:
            await message.answer(f"❌ خطای شبکه در بازار کالا: {str(e)}")
