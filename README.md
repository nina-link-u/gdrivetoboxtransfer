# gdrivetoboxtransfer

The script goes through folder Shogakukan at Google drive and transfers folders with files from under folder Lettering from each manga title to 【翻訳マスターデータ】マンガ翻訳 at box. The script compares name between titles by lowring the rgister and checking if names match. After finding the correct title, the scripts checks if appropriate chapter range folder, for example, 1-100 for chapter 50 exists, if not - creates if. Then checks if there is already folder for this chapter and if files inside both folers between google and box matches. If not - creates and names folder 0050 and transfer files. 

If chapter range folder exists with - or _ between numbers - new folder wouldn't be created. If none appropriate chapter folder was found  - creates one with _, for example 301_400. 

To avoid lost files when connection was stopped, files are copying with retry and timeout of 20 s and 10 tries. Usually enough to avoid Box restriction to multiple requests. 

Box login tokens are being refreshed authomatically.

Google drive login done by creating token.json. 
