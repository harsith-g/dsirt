import json
import re

CANDIDATES_RAW = """
MOHANAPRIYA R,monapatho@gmail.com
Ranichandra S,raniresearch2023@gmail.com
LOHANATHAN SUBBUREDDY,subalohu@gmail.com
PUROSKHAN SIDDIG,puroskh@gmail.com
Eniyasri Ravichandran,iniyasims@gmail.com
SELVI SANJEEVIRAYAN,sanjeevselvi3@gmail.com
AYESHA Kather,ayeshaahamath@gmail.com
AGNES AMALA ANITHA T,agnes.professional.15@gmail.com
Tamizh vendhan,tamizhvendhan2@gmail.com
T MANJU DEVI,manju782000@gmail.com
K.KAVITHA K.KAVITHA,kkavi.vi@gmail.com
R.PUNITHA R.PUNITHA,maths20.dscasw@gmail.com
DEEPA DHANAGOPAL,pandidrdsoundarpandi@gmail.com
Fizza S,fizzaagri@gmail.com
SUMITHRA R,meetsumithra.rani@gmail.com
RAJKUMAR R,sumiamity95@gmail.com
ANITHA M,ani.soni90@gmail.com
GOMATHI GANESAN,mahagomu12@gmail.com
SHIYAMALA M,shiyamalamuru@gmail.com
Janaki D,dorai.janaki@gmail.com
Vidhya Vijaya Kumar,vidhyavijayan0404@gmail.com
Ramya M,ramyasivakumar78@gmail.com
LAVANYA R,lavanya5148@gmail.com
SATHISHKUMAR P,sathishagri7777@gmail.com
RAJESWARI S,rajineha824@gmail.com
SEMILA MURUGESAN,smartcommerce19@gmail.com
RAJA. M,questionpapercommerce22@gmail.com
Siva Dharshini T,sdharshini.2699@gmail.com
PRIYA T,priyamscmphil29@gmail.com
Shiny D,shinyamalan@gmail.com
AMIRTHAVARSHINI S,amirthavarshinis728@gmail.com
Sangeetha C,chezhiansangeetha77@gmail.com
GOWTHAMI P,gowthamiarryan@gmail.com
PRIYATHARSINI D,pribindhu@gmail.com
AZHAKESWARI CHAKKARAPANI,azhakeswari1984@gmail.com
Anubama B,anudhanusri@gmail.com
Dhivya Ramesh,dhivyaramesh2656@gmail.com
Kiruthika A,venkatkiruthi183@gmail.com
Suganthi M,suganthi.1506@gmail.com
Karthik ANBAZHAGAN,karthickhmct@gmail.com
PREETHI J,preethiprakhash11@gmail.com
RAJESHWARI K Senthil,maharajcs@gmail.com
Anusha R,anusha14232001@gmail.com
Arunthathi M,msswathi2001@gmail.com
UMA S,umakani2511@gmail.com
SHARIN N,sharinnasarali@gmail.com
VINITHRA A,vinithra127@gmail.com
MADHUMITHA R,madhumithar447@gmail.com
Bhava tha rani,bavadharanibalraj@gmail.com
AMEER.H Haniffah,ameernasrin786@gmail.com
"""

candidates = []
for line in CANDIDATES_RAW.strip().splitlines():
    if not line.strip(): continue
    name, email = line.split(",", 1)
    candidates.append({
        "full_name": name.strip(),
        "email": email.strip().lower()
    })

with open("data/candidates.json", "w", encoding="utf-8") as f:
    json.dump(candidates, f, indent=2)

print(f"Successfully generated data/candidates.json with {len(candidates)} candidates.")
